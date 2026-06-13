from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from starlette.middleware.sessions import SessionMiddleware

from .auth import current_user, require_role, upsert_user_from_claims
from .config import Settings
from .db import Base, build_sessionmaker
from .models import Conversation, FeishuDelivery, ImageAsset, Message, Role, TeacherProfile, TeachingRoute, User
from .realtime import ConnectionManager
from .schemas import (
    ConversationCreate,
    DevLoginRequest,
    FeishuDeliveryOut,
    FeishuReplyEvent,
    MessageCreate,
    RouteCreate,
    TeacherUpdate,
    UserOut,
)
from .services import (
    add_message,
    conversation_out,
    create_student_conversation,
    get_conversation_for_user,
    handle_feishu_reply,
    image_out,
    message_out,
    teacher_inbox,
    upload_image,
)


def create_app(database_url: str | None = None, media_dir: Path | None = None, dev_auth_enabled: bool | None = None) -> FastAPI:
    settings = Settings()
    if database_url:
        settings.database_url = database_url
    if media_dir:
        settings.media_dir = media_dir
    if dev_auth_enabled is not None:
        settings.dev_auth_enabled = dev_auth_enabled

    session_factory = build_sessionmaker(settings.database_url)
    engine = session_factory.kw["bind"]
    Base.metadata.create_all(engine)

    app = FastAPI(title=settings.app_name)
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.manager = ConnectionManager()

    def db_session():
        db = session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    app.state.db = lambda: next(db_session())

    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, same_site="lax")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )

    @app.get("/health")
    def health(db: Session = Depends(db_session)):
        media_ok = True
        try:
            settings.media_dir.mkdir(parents=True, exist_ok=True)
            probe = settings.media_dir / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError:
            media_ok = False
        db.execute(select(User.id).limit(1)).all()
        return {
            "status": "ok" if media_ok else "degraded",
            "database": "ok",
            "media": "ok" if media_ok else "not_writable",
            "oidc_issuer": settings.oidc_issuer,
            "feishu_worker": "configured" if settings.feishu_app_id and settings.feishu_app_secret else "not_configured",
        }

    @app.post("/api/dev/login")
    def dev_login(payload: DevLoginRequest, request: Request, db: Session = Depends(db_session)):
        if not settings.dev_auth_enabled:
            raise HTTPException(status_code=404, detail="Not found")
        user = upsert_user_from_claims(db, payload.model_dump(exclude_none=True))
        db.commit()
        request.session["user_id"] = user.id
        return {"user": UserOut.model_validate(user, from_attributes=True).model_dump()}

    @app.get("/api/auth/oidc/login")
    def oidc_login(request: Request):
        state = f"im-{Path(str(settings.media_dir)).name}-{id(request)}"
        request.session["oidc_state"] = state
        params = urlencode(
            {
                "client_id": settings.oidc_client_id,
                "redirect_uri": settings.oidc_redirect_uri,
                "response_type": "code",
                "scope": "openid profile email",
                "state": state,
            }
        )
        return RedirectResponse(url=f"{settings.oidc_issuer}/protocol/openid-connect/auth?{params}")

    @app.get("/api/auth/oidc/callback")
    async def oidc_callback(code: str, state: str, request: Request, db: Session = Depends(db_session)):
        expected_state = request.session.get("oidc_state")
        if not expected_state or expected_state != state:
            raise HTTPException(status_code=400, detail="Invalid OIDC state")
        token_url = f"{settings.oidc_issuer}/protocol/openid-connect/token"
        userinfo_url = f"{settings.oidc_issuer}/protocol/openid-connect/userinfo"
        async with httpx.AsyncClient(timeout=10.0) as client:
            token_response = await client.post(
                token_url,
                data={
                    "grant_type": "authorization_code",
                    "client_id": settings.oidc_client_id,
                    "client_secret": settings.oidc_client_secret,
                    "redirect_uri": settings.oidc_redirect_uri,
                    "code": code,
                },
            )
            if token_response.status_code >= 400:
                raise HTTPException(status_code=400, detail="OIDC token exchange failed")
            access_token = token_response.json().get("access_token")
            userinfo_response = await client.get(userinfo_url, headers={"Authorization": f"Bearer {access_token}"})
            if userinfo_response.status_code >= 400:
                raise HTTPException(status_code=400, detail="OIDC userinfo failed")
        user = upsert_user_from_claims(db, userinfo_response.json())
        db.commit()
        request.session["user_id"] = user.id
        request.session.pop("oidc_state", None)
        return RedirectResponse(url="/im/")

    @app.post("/api/auth/logout")
    def logout(request: Request):
        request.session.clear()
        return {"ok": True}

    @app.get("/api/me", response_model=UserOut)
    def me(user: User = Depends(current_user)):
        return user

    @app.get("/api/teachers")
    def teachers(db: Session = Depends(db_session), _: User = Depends(current_user)):
        rows = (
            db.scalars(
                select(User)
                .join(TeacherProfile)
                .where(User.role == Role.teacher.value, TeacherProfile.enabled.is_(True))
                .order_by(User.display_name)
            )
            .unique()
            .all()
        )
        return [UserOut.model_validate(row, from_attributes=True).model_dump() for row in rows]

    @app.get("/api/subjects")
    def subjects(class_id: str, db: Session = Depends(db_session), _: User = Depends(current_user)):
        routes = db.scalars(select(TeachingRoute).where(TeachingRoute.class_id == class_id).order_by(TeachingRoute.subject)).all()
        return [{"subject": route.subject, "teacher_id": route.teacher_id} for route in routes]

    @app.post("/api/uploads/images")
    def upload(file: UploadFile = File(...), db: Session = Depends(db_session), user: User = Depends(current_user)):
        asset = upload_image(db, settings.media_dir, user, file)
        db.commit()
        return image_out(asset)

    @app.get("/api/media/{image_id}")
    def media(image_id: int, db: Session = Depends(db_session), user: User = Depends(current_user)):
        asset = db.get(ImageAsset, image_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="图片不存在")
        path = settings.media_dir / asset.path
        if not path.exists():
            raise HTTPException(status_code=404, detail="图片文件不存在")
        return FileResponse(path, media_type=asset.mime_type, filename=asset.original_name)

    @app.post("/api/conversations")
    async def create_conversation(
        payload: ConversationCreate,
        db: Session = Depends(db_session),
        user: User = Depends(require_role(Role.student)),
    ):
        conversation = create_student_conversation(db, user, payload)
        db.commit()
        hydrated = get_conversation_for_user(db, conversation.id, user)
        out = conversation_out(db, hydrated, user)
        await app.state.manager.send_to_user(out.teacher_id, "conversation.updated", out.model_dump(mode="json"))
        return out

    @app.get("/api/conversations/{conversation_id}/messages")
    def list_messages(conversation_id: int, db: Session = Depends(db_session), user: User = Depends(current_user)):
        conversation = get_conversation_for_user(db, conversation_id, user)
        return [message_out(message) for message in conversation.messages]

    @app.post("/api/conversations/{conversation_id}/messages")
    async def post_message(
        conversation_id: int,
        payload: MessageCreate,
        db: Session = Depends(db_session),
        user: User = Depends(current_user),
    ):
        conversation = get_conversation_for_user(db, conversation_id, user)
        message = add_message(db, conversation, user, payload)
        db.commit()
        out = message_out(message)
        peer_id = conversation.teacher_id if user.id == conversation.student_id else conversation.student_id
        await app.state.manager.send_to_user(peer_id, "message.created", out.model_dump(mode="json"))
        return out

    @app.get("/api/teacher/inbox")
    def inbox(db: Session = Depends(db_session), teacher: User = Depends(require_role(Role.teacher))):
        return teacher_inbox(db, teacher)

    @app.post("/api/conversations/{conversation_id}/assign")
    def assign_conversation(
        conversation_id: int,
        payload: dict,
        db: Session = Depends(db_session),
        _: User = Depends(require_role(Role.teacher, Role.admin)),
    ):
        conversation = db.get(Conversation, conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        teacher_id = int(payload.get("teacher_id"))
        teacher = db.get(User, teacher_id)
        if teacher is None or teacher.role != Role.teacher.value:
            raise HTTPException(status_code=404, detail="教师不存在")
        conversation.teacher_id = teacher.id
        db.commit()
        return {"ok": True}

    @app.post("/api/conversations/{conversation_id}/close")
    def close_conversation(conversation_id: int, db: Session = Depends(db_session), user: User = Depends(require_role(Role.teacher, Role.admin))):
        conversation = get_conversation_for_user(db, conversation_id, user)
        conversation.status = "closed"
        db.commit()
        return conversation_out(db, conversation, user)

    @app.put("/api/admin/teachers/{teacher_id}")
    def update_teacher(
        teacher_id: int,
        payload: TeacherUpdate,
        db: Session = Depends(db_session),
        _: User = Depends(require_role(Role.admin)),
    ):
        teacher = db.get(User, teacher_id)
        if teacher is None or teacher.role != Role.teacher.value:
            raise HTTPException(status_code=404, detail="教师不存在")
        if teacher.teacher_profile is None:
            teacher.teacher_profile = TeacherProfile(user_id=teacher.id)
        teacher.teacher_profile.enabled = payload.enabled
        teacher.teacher_profile.feishu_open_id = payload.feishu_open_id
        teacher.teacher_profile.feishu_user_id = payload.feishu_user_id
        db.commit()
        return {"ok": True}

    @app.post("/api/admin/routes")
    def create_route(payload: RouteCreate, db: Session = Depends(db_session), _: User = Depends(require_role(Role.admin))):
        teacher = db.get(User, payload.teacher_id)
        if teacher is None or teacher.role != Role.teacher.value:
            raise HTTPException(status_code=404, detail="教师不存在")
        route = db.scalar(select(TeachingRoute).where(TeachingRoute.class_id == payload.class_id, TeachingRoute.subject == payload.subject))
        if route is None:
            route = TeachingRoute(class_id=payload.class_id, subject=payload.subject, teacher_id=payload.teacher_id)
            db.add(route)
        else:
            route.teacher_id = payload.teacher_id
        db.commit()
        return {"id": route.id, "class_id": route.class_id, "subject": route.subject, "teacher_id": route.teacher_id}

    @app.get("/api/admin/routes")
    def list_routes(db: Session = Depends(db_session), _: User = Depends(require_role(Role.admin))):
        routes = db.scalars(select(TeachingRoute).order_by(TeachingRoute.class_id, TeachingRoute.subject)).all()
        return [{"id": route.id, "class_id": route.class_id, "subject": route.subject, "teacher_id": route.teacher_id} for route in routes]

    @app.delete("/api/admin/routes/{route_id}")
    def delete_route(route_id: int, db: Session = Depends(db_session), _: User = Depends(require_role(Role.admin))):
        route = db.get(TeachingRoute, route_id)
        if route is None:
            raise HTTPException(status_code=404, detail="路由不存在")
        db.delete(route)
        db.commit()
        return {"ok": True}

    @app.get("/api/admin/feishu/status")
    def feishu_status(db: Session = Depends(db_session), _: User = Depends(require_role(Role.admin))):
        deliveries = db.scalars(select(FeishuDelivery).order_by(FeishuDelivery.created_at.desc())).all()
        return {
            "worker": "configured" if settings.feishu_app_id and settings.feishu_app_secret else "not_configured",
            "deliveries": [FeishuDeliveryOut.model_validate(item, from_attributes=True).model_dump() for item in deliveries],
        }

    @app.post("/api/admin/feishu/retry/{message_id}")
    def retry_feishu(message_id: int, db: Session = Depends(db_session), _: User = Depends(require_role(Role.admin))):
        delivery = db.scalar(select(FeishuDelivery).where(FeishuDelivery.message_id == message_id))
        if delivery is None:
            raise HTTPException(status_code=404, detail="飞书投递记录不存在")
        delivery.status = "queued"
        delivery.error = None
        db.commit()
        return {"ok": True}

    @app.post("/api/dev/feishu/reply")
    async def dev_feishu_reply(payload: FeishuReplyEvent, db: Session = Depends(db_session)):
        if not settings.dev_auth_enabled:
            raise HTTPException(status_code=404, detail="Not found")
        message = handle_feishu_reply(
            db,
            payload.reply_to_message_id,
            payload.sender_open_id,
            payload.content,
            payload.image_ids,
        )
        db.commit()
        out = message_out(message)
        await app.state.manager.send_to_user(message.conversation.student_id, "message.created", out.model_dump(mode="json"))
        return out

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        session = websocket.session
        user_id = session.get("user_id")
        if not user_id:
            await websocket.close(code=4401)
            return
        await app.state.manager.connect(int(user_id), websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            app.state.manager.disconnect(int(user_id), websocket)

    return app
