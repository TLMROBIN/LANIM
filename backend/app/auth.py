from __future__ import annotations

from typing import Any

from collections.abc import Generator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Role, TeacherProfile, User


def first_attr(attributes: dict[str, Any], key: str) -> str | None:
    value = attributes.get(key)
    if isinstance(value, list):
        return str(value[0]) if value else None
    if value is None:
        return None
    return str(value)


def role_from_claims(claims: dict[str, Any]) -> str:
    attributes = claims.get("attributes") or {}
    role = (first_attr(attributes, "role") or claims.get("role") or "").lower()
    if role not in {item.value for item in Role}:
        raise HTTPException(status_code=400, detail="OIDC claims missing valid role")
    return str(role)


def upsert_user_from_claims(session: Session, claims: dict[str, Any]) -> User:
    sub = claims.get("sub")
    if not sub:
        raise HTTPException(status_code=400, detail="OIDC claims missing sub")
    role = role_from_claims(claims)
    attributes = claims.get("attributes") or {}
    username = claims.get("preferred_username") or claims.get("email") or sub
    display_name = claims.get("name") or claims.get("firstName") or username
    user = session.scalar(select(User).where(User.oidc_sub == sub))
    if user is None:
        user = User(
            oidc_sub=sub,
            username=username,
            display_name=display_name,
            role=role,
        )
        session.add(user)
        session.flush()
    user.username = username
    user.display_name = display_name
    user.role = role
    user.class_id = first_attr(attributes, "class_id") or first_attr(attributes, "class") or first_attr(attributes, "grade")
    user.grade = first_attr(attributes, "grade")
    if role == Role.teacher.value and user.teacher_profile is None:
        session.add(TeacherProfile(user_id=user.id, enabled=True))
        session.flush()
    return user


def get_db(request: Request) -> Generator[Session, None, None]:
    db = request.app.state.session_factory()
    try:
        yield db
    finally:
        db.close()


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    user = db.get(User, int(user_id))
    if user is None:
        request.session.clear()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


def require_role(*roles: Role):
    allowed = {role.value for role in roles}

    def dependency(user: User = Depends(current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        return user

    return dependency
