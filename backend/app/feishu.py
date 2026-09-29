from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from .config import Settings
from .models import FeishuDelivery, FeishuDeliveryStatus, Message

logger = logging.getLogger(__name__)


@dataclass
class FeishuClient:
    settings: Settings

    async def tenant_access_token(self) -> str:
        if not self.settings.feishu_app_id or not self.settings.feishu_app_secret:
            raise RuntimeError("Feishu app_id/app_secret is not configured")
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={
                    "app_id": self.settings.feishu_app_id,
                    "app_secret": self.settings.feishu_app_secret,
                },
            )
            response.raise_for_status()
            body = response.json()
            if body.get("code") != 0:
                raise RuntimeError(f"Feishu token error: {body}")
            return body["tenant_access_token"]

    async def send_text(self, receive_id: str, text: str) -> str:
        token = await self.tenant_access_token()
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                "https://open.feishu.cn/open-apis/im/v1/messages",
                params={"receive_id_type": "open_id"},
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "receive_id": receive_id,
                    "msg_type": "text",
                    "content": json.dumps({"text": text}, ensure_ascii=False),
                },
            )
            response.raise_for_status()
            body = response.json()
            if body.get("code") != 0:
                raise RuntimeError(f"Feishu send error: {body}")
            return body["data"]["message_id"]

    async def upload_image(self, image: bytes, filename: str, mime_type: str) -> str:
        token = await self.tenant_access_token()
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://open.feishu.cn/open-apis/im/v1/images",
                headers={"Authorization": f"Bearer {token}"},
                data={"image_type": "message"},
                files={"image": (filename, image, mime_type)},
            )
            response.raise_for_status()
            body = response.json()
            if body.get("code") != 0:
                raise RuntimeError(f"Feishu image upload error: {body}")
            return body["data"]["image_key"]

    async def send_post(self, receive_id: str, title: str, rows: list[list[dict[str, str]]]) -> str:
        token = await self.tenant_access_token()
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                "https://open.feishu.cn/open-apis/im/v1/messages",
                params={"receive_id_type": "open_id"},
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "receive_id": receive_id,
                    "msg_type": "post",
                    "content": json.dumps(
                        {"zh_cn": {"title": title, "content": rows}}, ensure_ascii=False
                    ),
                },
            )
            response.raise_for_status()
            body = response.json()
            if body.get("code") != 0:
                raise RuntimeError(f"Feishu send error: {body}")
            return body["data"]["message_id"]

    async def download_message_resource(self, message_id: str, file_key: str, resource_type: str = "image") -> tuple[bytes, str]:
        token = await self.tenant_access_token()
        url = (
            "https://open.feishu.cn/open-apis/im/v1/messages/"
            f"{quote(message_id, safe='')}/resources/{quote(file_key, safe='')}"
        )
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                url,
                params={"type": resource_type},
                headers={"Authorization": f"Bearer {token}"},
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                try:
                    error_body = response.json()
                except ValueError:
                    error_body = {}
                code = error_body.get("code")
                message = error_body.get("msg") or error_body.get("message")
                details = []
                if code is not None:
                    details.append(f"code {code}")
                if message:
                    details.append(str(message))
                suffix = f" ({'; '.join(details)})" if details else ""
                raise RuntimeError(f"Feishu resource download returned HTTP {response.status_code}{suffix}") from exc
            return response.content, response.headers.get("content-type", "image/jpeg")

    async def resolve_open_id_by_mobile(self, mobile: str) -> str:
        """Resolve one teacher's app-scoped open_id from a mobile number."""
        token = await self.tenant_access_token()
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                "https://open.feishu.cn/open-apis/contact/v3/users/batch_get_id",
                params={"user_id_type": "open_id"},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                json={"mobiles": [mobile]},
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                try:
                    error_body = response.json()
                except ValueError:
                    error_body = {}
                code = error_body.get("code")
                message = error_body.get("msg") or error_body.get("message")
                details = []
                if code is not None:
                    details.append(f"code {code}")
                if message:
                    details.append(str(message))
                suffix = f" ({'; '.join(details)})" if details else ""
                raise RuntimeError(f"飞书通讯录接口返回 HTTP {response.status_code}{suffix}") from exc
            body = response.json()
            if body.get("code") != 0:
                code = body.get("code", "unknown")
                message = body.get("msg") or body.get("message") or "未知错误"
                raise RuntimeError(f"飞书通讯录接口错误 code {code}: {message}")
            users = (body.get("data") or {}).get("user_list") or []
            if len(users) != 1 or not users[0].get("user_id"):
                raise LookupError("未找到与该手机号匹配的飞书用户")
            return users[0]["user_id"]


async def flush_queued_deliveries(session_factory: sessionmaker[Session], settings: Settings) -> int:
    client = FeishuClient(settings)
    sent = 0
    with session_factory() as session:
        deliveries = session.scalars(
            select(FeishuDelivery)
            .options(selectinload(FeishuDelivery.message).selectinload(Message.images))
            .where(FeishuDelivery.status == FeishuDeliveryStatus.queued.value)
        ).all()
        for delivery in deliveries:
            if not delivery.feishu_open_id:
                delivery.status = FeishuDeliveryStatus.failed.value
                delivery.error = "Teacher has no feishu_open_id"
                continue
            try:
                message = delivery.message
                conversation = delivery.conversation
                text = (
                    f"学生：{conversation.student.display_name}\n"
                    f"科目：{conversation.subject or '未指定'}\n"
                    f"内容：{message.content or '（见图片）'}"
                )
                if message.images:
                    rows: list[list[dict[str, str]]] = [[{"tag": "text", "text": text}]]
                    for image_asset in message.images:
                        image_data = (settings.media_dir / image_asset.path).read_bytes()
                        image_key = await client.upload_image(
                            image_data,
                            image_asset.original_name,
                            image_asset.mime_type,
                        )
                        rows.append([{"tag": "img", "image_key": image_key}])
                    rows.append(
                        [{"tag": "text", "text": "请直接回复本条机器人消息，系统会同步给学生。"}]
                    )
                    title = f"学生提问 · {conversation.subject or '未指定科目'}"
                    delivery.feishu_message_id = await client.send_post(
                        delivery.feishu_open_id, title, rows
                    )
                else:
                    text += "\n\n请直接回复本条机器人消息，系统会同步给学生。"
                    delivery.feishu_message_id = await client.send_text(delivery.feishu_open_id, text)
                delivery.status = FeishuDeliveryStatus.sent.value
                delivery.error = None
                sent += 1
            except Exception as exc:  # pragma: no cover - depends on Feishu network
                logger.exception("failed to send Feishu delivery %s", delivery.id)
                delivery.status = FeishuDeliveryStatus.failed.value
                delivery.error = str(exc)
        session.commit()
    return sent


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _message_content(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"text": value}
        return parsed if isinstance(parsed, dict) else {"text": value}
    return {}


def extract_reply_event(event: Any) -> dict[str, Any] | None:
    envelope = _field(event, "event") or event
    message = _field(envelope, "message") or _field(event, "message")
    sender = _field(envelope, "sender") or _field(event, "sender")
    if message is None or sender is None:
        return None
    sender_id = _field(sender, "sender_id")
    parent_id = _field(message, "parent_id") or _field(message, "root_id")
    sender_open_id = _field(sender_id, "open_id")
    content = _message_content(_field(message, "content"))
    text = content.get("text") or ""
    image_key = content.get("image_key") or ""
    message_type = _field(message, "message_type") or ("image" if image_key else "text" if text else "")
    message_id = _field(message, "message_id") or ""
    if not parent_id or not sender_open_id:
        return None
    return {
        "reply_to_message_id": str(parent_id),
        "sender_open_id": str(sender_open_id),
        "message_id": str(message_id),
        "message_type": str(message_type),
        "image_key": str(image_key),
        "content": str(text),
    }
