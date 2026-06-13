from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .models import FeishuDelivery, FeishuDeliveryStatus

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


async def flush_queued_deliveries(session_factory: sessionmaker[Session], settings: Settings) -> int:
    client = FeishuClient(settings)
    sent = 0
    with session_factory() as session:
        deliveries = session.scalars(
            select(FeishuDelivery).where(FeishuDelivery.status == FeishuDeliveryStatus.queued.value)
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
                    f"内容：{message.content}\n\n"
                    "请直接回复本条机器人消息，系统会同步给学生。"
                )
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


def extract_reply_event(event: dict[str, Any]) -> dict[str, str] | None:
    message = event.get("event", {}).get("message") or event.get("message")
    sender = event.get("event", {}).get("sender") or event.get("sender")
    if not message or not sender:
        return None
    parent_id = message.get("parent_id") or message.get("root_id")
    sender_id = (sender.get("sender_id") or {}).get("open_id")
    content = message.get("content") or "{}"
    try:
        text = json.loads(content).get("text", "")
    except json.JSONDecodeError:
        text = content
    if not parent_id or not sender_id or not text:
        return None
    return {"reply_to_message_id": parent_id, "sender_open_id": sender_id, "content": text}
