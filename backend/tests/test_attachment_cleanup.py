import io
from datetime import datetime, timedelta

from sqlalchemy import select

from app.attachment_cleanup import cleanup_attachments
from app.models import FeishuDelivery, ImageAsset, Message, User
from test_core import client, login


def test_large_camera_upload_and_cleanup_preserves_history(client):
    login(client, 'student', 'photo-student', '拍照学生', class_id=['高一1班'])
    data = b'x' * (12 * 1024 * 1024)
    upload = client.post('/api/uploads/images', files={'file': ('photo.jpg', io.BytesIO(data), 'image/jpeg')})
    assert upload.status_code == 200
    asset_id = upload.json()['id']
    settings = client.app.state.settings
    now = datetime.utcnow()
    with client.app.state.session_factory() as db:
        user = db.scalar(select(User))
        asset = db.get(ImageAsset, asset_id)
        asset.created_at = now - timedelta(days=100)
        old_path = settings.media_dir / asset.path
        # An old upload attached to a recent message must survive.
        message = Message(conversation_id=1, sender_id=user.id, sender_role='student', source='web',
                          content='保留文字', created_at=now)
        db.add(message)
        db.flush()
        asset.message_id = message.id
        db.commit()
        assert cleanup_attachments(db, settings, now=now)['files'] == 0
        message.created_at = now - timedelta(days=100)
        db.add(FeishuDelivery(conversation_id=1, message_id=message.id, teacher_id=user.id,
                              feishu_message_id='pending-photo', status='queued'))
        db.commit()
        assert cleanup_attachments(db, settings, now=now)['files'] == 0
        delivery = db.scalar(select(FeishuDelivery))
        delivery.status = 'sent'
        db.commit()
        preview = cleanup_attachments(db, settings, now=now, dry_run=True)
        assert preview['files'] == 1 and preview['bytes'] == len(data)
        assert old_path.exists() and asset.path
        result = cleanup_attachments(db, settings, now=now)
        assert result['files'] == 1 and result['bytes'] == len(data)
        assert not old_path.exists()
        assert db.get(Message, message.id).content == '保留文字'
        assert cleanup_attachments(db, settings, now=now)['files'] == 0
    response = client.get(f'/api/media/{asset_id}')
    assert response.status_code == 410


def test_cleanup_keeps_recent_files_and_rejects_outside_paths(client, tmp_path):
    login(client, 'student', 'retention-student', '学生')
    settings = client.app.state.settings
    outside = tmp_path / 'outside.jpg'
    outside.write_bytes(b'keep')
    now = datetime.utcnow()
    with client.app.state.session_factory() as db:
        user = db.scalar(select(User))
        for path, age in [(str(outside), 100), ('new.jpg', 1), ('boundary.jpg', 30), ('missing.jpg', 100)]:
            db.add(ImageAsset(owner_id=user.id, original_name='photo.jpg', path=path, size=4,
                              mime_type='image/jpeg', created_at=now-timedelta(days=age)))
        db.commit()
        result = cleanup_attachments(db, settings, now=now)
        assert result['files'] == 1 and result['skipped'] == 1
        assert outside.read_bytes() == b'keep'
        assert db.scalar(select(ImageAsset).where(ImageAsset.path == 'new.jpg'))
        assert db.scalar(select(ImageAsset).where(ImageAsset.path == 'boundary.jpg'))
