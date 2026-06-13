from __future__ import annotations

import asyncio
import json
import logging
import threading

import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

from .config import Settings
from .db import Base, build_sessionmaker
from .feishu import extract_reply_event, flush_queued_deliveries
from .services import handle_feishu_reply


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("school-im.feishu-worker")


async def delivery_loop(session_factory, settings: Settings) -> None:
    while True:
        sent = await flush_queued_deliveries(session_factory, settings)
        if sent:
            logger.info("sent %s queued Feishu messages", sent)
        await asyncio.sleep(5)


def start_delivery_loop(session_factory, settings: Settings) -> None:
    asyncio.run(delivery_loop(session_factory, settings))


def start_long_connection(session_factory, settings: Settings) -> None:
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        logger.warning("Feishu app credentials are missing; long connection disabled")
        while True:
            threading.Event().wait(3600)
        return

    def on_message_receive(event: P2ImMessageReceiveV1) -> None:
        payload = extract_reply_event(event.__dict__)
        if payload is None:
            event_data = getattr(event, "event", None)
            sender = getattr(event_data, "sender", None)
            message = getattr(event_data, "message", None)
            sender_id = getattr(sender, "sender_id", None)
            payload = {
                "reply_to_message_id": getattr(message, "parent_id", None) or getattr(message, "root_id", None),
                "sender_open_id": getattr(sender_id, "open_id", None),
                "content": (getattr(message, "content", "") or "").strip(),
            }
            try:
                payload["content"] = json.loads(payload["content"]).get("text", payload["content"])
            except json.JSONDecodeError:
                pass
        if not payload.get("reply_to_message_id") or not payload.get("sender_open_id") or not payload.get("content"):
            logger.info("ignored Feishu message without reply mapping")
            return
        with session_factory() as session:
            handle_feishu_reply(
                session,
                payload["reply_to_message_id"],
                payload["sender_open_id"],
                payload["content"],
            )
            session.commit()
            logger.info("synced Feishu reply for %s", payload["reply_to_message_id"])

    handler = (
        lark.EventDispatcherHandler.builder(
            settings.feishu_encrypt_key or "",
            settings.feishu_verification_token or "",
        )
        .register_p2_im_message_receive_v1(on_message_receive)
        .build()
    )
    client = lark.ws.Client(settings.feishu_app_id, settings.feishu_app_secret, event_handler=handler)
    logger.info("Feishu long connection starting")
    client.start()


async def main() -> None:
    settings = Settings()
    session_factory = build_sessionmaker(settings.database_url)
    Base.metadata.create_all(session_factory.kw["bind"])
    logger.info("Feishu worker started")
    threading.Thread(target=start_delivery_loop, args=(session_factory, settings), daemon=True).start()
    start_long_connection(session_factory, settings)


if __name__ == "__main__":
    asyncio.run(main())
