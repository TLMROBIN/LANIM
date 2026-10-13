"""Monthly attachment retention; metadata and messages remain readable."""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .db import build_sessionmaker
from .models import FeishuDelivery, ImageAsset, Message

logger = logging.getLogger(__name__)
SHANGHAI = timezone(timedelta(hours=8))


def cleanup_attachments(session: Session, settings: Settings, *, now: datetime | None = None,
                        dry_run: bool = False) -> dict:
    if settings.attachment_retention_days < 1:
        raise ValueError("attachment_retention_days must be positive")
    cutoff = (now or datetime.utcnow()) - timedelta(days=settings.attachment_retention_days)
    # Protect images whose message is newer than its upload, or still queued for delivery.
    pending = select(FeishuDelivery.message_id).where(FeishuDelivery.status == "queued")
    recent = select(Message.id).where(Message.created_at >= cutoff)
    candidates = session.scalars(select(ImageAsset).where(
        ImageAsset.created_at < cutoff, ImageAsset.path != "",
        (ImageAsset.message_id.is_(None)) | (
            ImageAsset.message_id.not_in(pending) & ImageAsset.message_id.not_in(recent)
        ),
    ).order_by(ImageAsset.id))
    root = settings.media_dir.resolve()
    result = {"dry_run": dry_run, "cutoff": cutoff.isoformat(), "files": 0, "bytes": 0, "skipped": 0}
    for asset in candidates:
        path = root / asset.path
        # Never follow links or delete outside the media volume.
        if path.is_symlink() or root not in path.resolve().parents:
            result["skipped"] += 1
            logger.warning("Unsafe attachment path skipped: asset %s", asset.id)
            continue
        try:
            size = path.stat().st_size if path.exists() else 0
            if not dry_run:
                path.unlink(missing_ok=True)
                asset.path = ""  # Tombstone without schema migration or loss of message history.
                asset.size = 0
                session.commit()
            result["files"] += 1
            result["bytes"] += size
        except OSError:
            logger.exception("Could not remove attachment %s", asset.id)
            result["skipped"] += 1
    return result


def run_monthly(settings: Settings) -> None:
    if settings.attachment_retention_days < 1:
        raise ValueError("attachment_retention_days must be positive")
    logger.info("Attachment cleanup enabled: retain %s days, monthly at 03:00 Asia/Shanghai",
                settings.attachment_retention_days)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    factory = build_sessionmaker(settings.database_url)
    marker = settings.media_dir / ".attachment-cleanup-month"
    with (settings.media_dir / ".attachment-cleanup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # First installation waits for the next monthly run instead of deleting immediately.
        if not marker.exists():
            marker.write_text(datetime.now(SHANGHAI).strftime("%Y-%m"))
        while True:
            now = datetime.now(SHANGHAI)
            month = now.strftime("%Y-%m")
            if marker.read_text().strip() != month and (now.day > 1 or now.hour >= 3):
                with factory() as session:
                    result = cleanup_attachments(session, settings, now=now.astimezone(timezone.utc).replace(tzinfo=None))
                logger.info("Monthly attachment cleanup: %s", json.dumps(result))
                if result["skipped"] == 0:
                    marker.write_text(month)
            time.sleep(3600)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--monthly", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    if args.monthly:
        run_monthly(settings)
    else:
        with build_sessionmaker(settings.database_url)() as session:
            print(json.dumps(cleanup_attachments(session, settings, dry_run=args.dry_run)))


if __name__ == "__main__":
    main()
