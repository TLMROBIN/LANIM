from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Role(str, Enum):
    student = "student"
    teacher = "teacher"
    admin = "admin"


class ConversationStatus(str, Enum):
    open = "open"
    closed = "closed"


class MessageSender(str, Enum):
    student = "student"
    teacher = "teacher"
    system = "system"


class MessageSource(str, Enum):
    web = "web"
    feishu = "feishu"


class FeishuDeliveryStatus(str, Enum):
    queued = "queued"
    sent = "sent"
    failed = "failed"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    oidc_sub: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(128), index=True)
    display_name: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(24), index=True)
    class_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    grade: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    teacher_profile: Mapped[Optional["TeacherProfile"]] = relationship(back_populates="user", uselist=False)


class TeacherProfile(Base):
    __tablename__ = "teacher_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    feishu_open_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    feishu_user_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)

    user: Mapped[User] = relationship(back_populates="teacher_profile")


class TeachingRoute(Base):
    __tablename__ = "teaching_routes"
    __table_args__ = (UniqueConstraint("class_id", "subject", name="uq_route_class_subject"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    class_id: Mapped[str] = mapped_column(String(128), index=True)
    subject: Mapped[str] = mapped_column(String(128), index=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    teacher: Mapped[User] = relationship()


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    subject: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    mode: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default=ConversationStatus.open.value)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    student: Mapped[User] = relationship(foreign_keys=[student_id])
    teacher: Mapped[User] = relationship(foreign_keys=[teacher_id])
    messages: Mapped[List["Message"]] = relationship(back_populates="conversation", order_by="Message.created_at")


class ImageAsset(Base):
    __tablename__ = "image_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    original_name: Mapped[str] = mapped_column(String(255))
    path: Mapped[str] = mapped_column(String(512))
    mime_type: Mapped[str] = mapped_column(String(128))
    size: Mapped[int] = mapped_column(Integer)
    message_id: Mapped[Optional[int]] = mapped_column(ForeignKey("messages.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    sender_role: Mapped[str] = mapped_column(String(24))
    source: Mapped[str] = mapped_column(String(24), default=MessageSource.web.value)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    sender: Mapped[User] = relationship()
    images: Mapped[List[ImageAsset]] = relationship()


class FeishuDelivery(Base):
    __tablename__ = "feishu_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), index=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    feishu_open_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    feishu_message_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default=FeishuDeliveryStatus.queued.value)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    conversation: Mapped[Conversation] = relationship()
    message: Mapped[Message] = relationship()
    teacher: Mapped[User] = relationship()
