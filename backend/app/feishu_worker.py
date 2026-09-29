from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

from .config import Settings
from .db import Base, build_sessionmaker
from .feishu import FeishuClient, extract_reply_event, flush_queued_deliveries
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


def process_feishu_reply(payload, session_factory, settings: Settings) -> None:
    message_type = payload["message_type"].lower()
    content = payload["content"]
    image_data = None
    image_mime_type = None
    if message_type == "image":
        image_key = payload["image_key"]
        message_id = payload["message_id"]
        if image_key and message_id:
            try:
                image_data, image_mime_type = asyncio.run(
                    FeishuClient(settings).download_message_resource(message_id, image_key)
                )
            except Exception:
                logger.exception("failed to download Feishu image resource for %s", message_id)
                content = "飞书图片同步失败，请重新发送文字说明。"
        else:
            content = "飞书图片信息不完整，请重新发送文字说明。"
    elif message_type != "text":
        logger.info("ignored unsupported Feishu reply type %s", message_type or "unknown")
        return
    if not content and image_data is None:
        logger.info("ignored empty Feishu reply to %s", payload["reply_to_message_id"])
        return
    try:
        with session_factory() as session:
            handle_feishu_reply(
                session,
                payload["reply_to_message_id"],
                payload["sender_open_id"],
                content,
                image_data=image_data,
                image_mime_type=image_mime_type,
                media_dir=settings.media_dir,
            )
            session.commit()
        logger.info("synced Feishu reply for %s", payload["reply_to_message_id"])
    except Exception:
        logger.exception("failed to sync Feishu reply for %s", payload["reply_to_message_id"])


def start_long_connection(session_factory, settings: Settings) -> None:
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        logger.warning("Feishu app credentials are missing; long connection disabled")
        while True:
            threading.Event().wait(3600)
        return

    reply_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="feishu-reply")

    def on_message_receive(event: P2ImMessageReceiveV1) -> None:
        payload = extract_reply_event(event)
        if payload is None:
            logger.info("ignored Feishu message without reply mapping")
            return
        # Lark dispatches callbacks on its running asyncio loop. Downloading an
        # image uses asyncio.run(), so move the full reply handler off that loop.
        reply_executor.submit(process_feishu_reply, payload, session_factory, settings)

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


def main() -> None:
    settings = Settings()
    session_factory = build_sessionmaker(settings.database_url)
    Base.metadata.create_all(session_factory.kw["bind"])
    logger.info("Feishu worker started")
    threading.Thread(target=start_delivery_loop, args=(session_factory, settings), daemon=True).start()
    start_long_connection(session_factory, settings)


if __name__ == "__main__":
    main()
