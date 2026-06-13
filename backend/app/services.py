from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .models import (
    Conversation,
    FeishuDelivery,
    FeishuDeliveryStatus,
    ImageAsset,
    Message,
    MessageSource,
    Role,
    TeacherProfile,
    TeachingRoute,
    User,
)
from .schemas import ConversationCreate, ConversationOut, ImageOut, MessageCreate, MessageOut


def ensure_teacher(session: Session, teacher_id: int) -> User:
    teacher = session.get(User, teacher_id)
    if teacher is None or teacher.role != Role.teacher.value:
        raise HTTPException(status_code=404, detail="教师不存在")
    profile = teacher.teacher_profile
    if profile is None or not profile.enabled:
        raise HTTPException(status_code=400, detail="教师未启用")
    return teacher


def upload_image(session: Session, media_dir: Path, user: User, file: UploadFile) -> ImageAsset:
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=400, detail="只支持图片上传")
    media_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "image").suffix or ".bin"
    relative = Path(str(user.id)) / f"{uuid.uuid4().hex}{suffix}"
    target = media_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as handle:
        shutil.copyfileobj(file.file, handle)
    asset = ImageAsset(
        owner_id=user.id,
        original_name=file.filename or "image",
        path=str(relative),
        mime_type=file.content_type or "application/octet-stream",
        size=target.stat().st_size,
    )
    session.add(asset)
    session.flush()
    return asset


def resolve_teacher(session: Session, student: User, data: ConversationCreate) -> User:
    if data.mode == "direct":
        if not data.teacher_id:
            raise HTTPException(status_code=400, detail="直接选择教师时必须提供 teacher_id")
        return ensure_teacher(session, data.teacher_id)
    if not student.class_id:
        raise HTTPException(status_code=400, detail="当前学生缺少班级信息，无法按班级科目分配")
    if not data.subject:
        raise HTTPException(status_code=400, detail="按科目分配时必须提供 subject")
    route = session.scalar(
        select(TeachingRoute).where(
            TeachingRoute.class_id == student.class_id,
            TeachingRoute.subject == data.subject,
        )
    )
    if route is None:
        raise HTTPException(status_code=404, detail=f"未配置 {student.class_id} / {data.subject} 的任课教师")
    return ensure_teacher(session, route.teacher_id)


def attach_images(session: Session, message: Message, image_ids: list[int], owner: User) -> None:
    for image_id in image_ids:
        asset = session.get(ImageAsset, image_id)
        if asset is None:
            raise HTTPException(status_code=404, detail=f"图片 {image_id} 不存在")
        if asset.owner_id != owner.id and owner.role != Role.teacher.value:
            raise HTTPException(status_code=403, detail="不能使用他人上传的图片")
        asset.message_id = message.id


def create_student_conversation(session: Session, student: User, data: ConversationCreate) -> Conversation:
    if student.role != Role.student.value:
        raise HTTPException(status_code=403, detail="Only students can create conversations")
    teacher = resolve_teacher(session, student, data)
    conversation = Conversation(
        student_id=student.id,
        teacher_id=teacher.id,
        subject=data.subject,
        mode=data.mode,
    )
    session.add(conversation)
    session.flush()
    message = Message(
        conversation_id=conversation.id,
        sender_id=student.id,
        sender_role=Role.student.value,
        source=MessageSource.web.value,
        content=data.content,
    )
    session.add(message)
    session.flush()
    attach_images(session, message, data.image_ids, student)
    create_feishu_delivery(session, conversation, message, teacher)
    session.flush()
    return conversation


def add_message(session: Session, conversation: Conversation, sender: User, data: MessageCreate, source: str = MessageSource.web.value) -> Message:
    if sender.id not in {conversation.student_id, conversation.teacher_id}:
        raise HTTPException(status_code=403, detail="不能访问该会话")
    message = Message(
        conversation_id=conversation.id,
        sender_id=sender.id,
        sender_role=sender.role,
        source=source,
        content=data.content,
    )
    session.add(message)
    session.flush()
    attach_images(session, message, data.image_ids, sender)
    return message


def create_feishu_delivery(session: Session, conversation: Conversation, message: Message, teacher: User) -> FeishuDelivery | None:
    profile = teacher.teacher_profile
    if profile is None or not profile.feishu_open_id:
        return None
    delivery = FeishuDelivery(
        conversation_id=conversation.id,
        message_id=message.id,
        teacher_id=teacher.id,
        feishu_open_id=profile.feishu_open_id,
        feishu_message_id=f"local-{message.id}-{uuid.uuid4().hex[:8]}",
        status=FeishuDeliveryStatus.queued.value,
    )
    session.add(delivery)
    return delivery


def image_out(asset: ImageAsset) -> ImageOut:
    return ImageOut(
        id=asset.id,
        url=f"/api/media/{asset.id}",
        original_name=asset.original_name,
        mime_type=asset.mime_type,
        size=asset.size,
    )


def message_out(message: Message) -> MessageOut:
    return MessageOut(
        id=message.id,
        conversation_id=message.conversation_id,
        sender_id=message.sender_id,
        sender_name=message.sender.display_name,
        sender_role=message.sender_role,
        source=message.source,
        content=message.content,
        created_at=message.created_at,
        images=[image_out(asset) for asset in message.images],
    )


def conversation_out(session: Session, conversation: Conversation, viewer: User | None = None) -> ConversationOut:
    last_message = conversation.messages[-1] if conversation.messages else None
    unread_count = 0
    if viewer and viewer.id == conversation.teacher_id:
        unread_count = session.scalar(
            select(func.count(Message.id)).where(
                Message.conversation_id == conversation.id,
                Message.sender_role == Role.student.value,
            )
        ) or 0
    return ConversationOut(
        id=conversation.id,
        student_id=conversation.student_id,
        student_name=conversation.student.display_name,
        teacher_id=conversation.teacher_id,
        teacher_name=conversation.teacher.display_name,
        subject=conversation.subject,
        mode=conversation.mode,
        status=conversation.status,
        unread_count=unread_count,
        last_message=message_out(last_message) if last_message else None,
    )


def get_conversation_for_user(session: Session, conversation_id: int, user: User) -> Conversation:
    conversation = session.scalar(
        select(Conversation)
        .options(
            selectinload(Conversation.student),
            selectinload(Conversation.teacher),
            selectinload(Conversation.messages).selectinload(Message.sender),
            selectinload(Conversation.messages).selectinload(Message.images),
        )
        .where(Conversation.id == conversation_id)
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if user.role != Role.admin.value and user.id not in {conversation.student_id, conversation.teacher_id}:
        raise HTTPException(status_code=403, detail="不能访问该会话")
    return conversation


def teacher_inbox(session: Session, teacher: User) -> list[ConversationOut]:
    conversations = (
        session.scalars(
            select(Conversation)
            .options(
                selectinload(Conversation.student),
                selectinload(Conversation.teacher),
                selectinload(Conversation.messages).selectinload(Message.sender),
                selectinload(Conversation.messages).selectinload(Message.images),
            )
            .where(Conversation.teacher_id == teacher.id)
            .order_by(Conversation.updated_at.desc())
        )
        .unique()
        .all()
    )
    return [conversation_out(session, item, teacher) for item in conversations]


def handle_feishu_reply(
    session: Session,
    reply_to_message_id: str,
    sender_open_id: str,
    content: str,
    image_ids: list[int] | None = None,
) -> Message:
    delivery = session.scalar(
        select(FeishuDelivery)
        .options(
            selectinload(FeishuDelivery.conversation),
            selectinload(FeishuDelivery.teacher).selectinload(User.teacher_profile),
        )
        .where(FeishuDelivery.feishu_message_id == reply_to_message_id)
    )
    if delivery is None:
        raise HTTPException(status_code=404, detail="无法找到飞书消息映射")
    profile = delivery.teacher.teacher_profile
    if profile is None or profile.feishu_open_id != sender_open_id:
        raise HTTPException(status_code=403, detail="飞书用户未绑定到该教师")
    return add_message(
        session,
        delivery.conversation,
        delivery.teacher,
        MessageCreate(content=content, image_ids=image_ids or []),
        source=MessageSource.feishu.value,
    )
