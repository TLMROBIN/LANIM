from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class DevLoginRequest(BaseModel):
    sub: str
    preferred_username: Optional[str] = None
    name: Optional[str] = None
    firstName: Optional[str] = None
    attributes: dict = Field(default_factory=dict)


class TeacherUpdate(BaseModel):
    enabled: bool = True
    feishu_open_id: Optional[str] = None
    feishu_user_id: Optional[str] = None


class TeacherProfileOut(BaseModel):
    enabled: bool
    feishu_open_id: Optional[str] = None
    feishu_user_id: Optional[str] = None


class AdminUserCreate(BaseModel):
    oidc_sub: Optional[str] = None
    username: str
    display_name: str
    role: Literal["student", "teacher", "admin"]
    class_id: Optional[str] = None
    grade: Optional[str] = None
    enabled: bool = True
    feishu_open_id: Optional[str] = None
    feishu_user_id: Optional[str] = None


class AdminUserUpdate(BaseModel):
    username: Optional[str] = None
    display_name: Optional[str] = None
    class_id: Optional[str] = None
    grade: Optional[str] = None
    enabled: Optional[bool] = None
    feishu_open_id: Optional[str] = None
    feishu_user_id: Optional[str] = None


class AdminUserSyncRequest(BaseModel):
    users: List[AdminUserCreate] = Field(default_factory=list)


class RouteCreate(BaseModel):
    class_id: str
    subject: str
    teacher_id: int


class ConversationCreate(BaseModel):
    mode: Literal["direct", "route"]
    teacher_id: Optional[int] = None
    subject: Optional[str] = None
    content: str
    image_ids: List[int] = Field(default_factory=list)


class MessageCreate(BaseModel):
    content: str
    image_ids: List[int] = Field(default_factory=list)


class FeishuReplyEvent(BaseModel):
    reply_to_message_id: str
    sender_open_id: str
    content: str
    image_ids: List[int] = Field(default_factory=list)


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: str
    class_id: Optional[str] = None
    grade: Optional[str] = None


class AdminUserOut(UserOut):
    oidc_sub: str
    teacher_profile: Optional[TeacherProfileOut] = None


class ImageOut(BaseModel):
    id: int
    url: str
    original_name: str
    mime_type: str
    size: int


class MessageOut(BaseModel):
    id: int
    conversation_id: int
    sender_id: int
    sender_name: str
    sender_role: str
    source: str
    content: str
    created_at: datetime
    images: List[ImageOut] = Field(default_factory=list)


class ConversationOut(BaseModel):
    id: int
    student_id: int
    student_name: str
    teacher_id: int
    teacher_name: str
    subject: Optional[str]
    mode: str
    status: str
    unread_count: int = 0
    last_message: Optional[MessageOut] = None


class FeishuDeliveryOut(BaseModel):
    id: int
    conversation_id: int
    message_id: int
    teacher_id: int
    feishu_open_id: Optional[str]
    feishu_message_id: str
    status: str
    error: Optional[str]
