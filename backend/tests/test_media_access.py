"""图片访问控制测试：/api/media/{id} 仅允许上传者、所属会话师生双方或管理员访问。"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    app = create_app(database_url="sqlite:///:memory:", media_dir=tmp_path / "media", dev_auth_enabled=True)
    return TestClient(app)


def login(client: TestClient, role: str, sub: str, name: str, **attrs):
    payload = {
        "sub": sub,
        "preferred_username": sub,
        "name": name,
        "attributes": {"role": [role], **attrs},
    }
    response = client.post("/api/dev/login", json=payload)
    assert response.status_code == 200
    return response


def _upload_image(client: TestClient) -> int:
    response = client.post(
        "/api/uploads/images",
        files={"file": ("photo.png", b"\x89PNG-fake-bytes", "image/png")},
    )
    assert response.status_code == 200
    return response.json()["id"]


def test_media_access_is_limited_to_conversation_members(client: TestClient):
    # 教师先登录建档
    login(client, "teacher", "tea-media", "李老师")
    teacher_id = client.get("/api/me").json()["id"]

    # 学生 A 上传图片并向教师提问
    login(client, "student", "stu-media-a", "学生甲", class_id=["高一1班"])
    image_id = _upload_image(client)
    response = client.post(
        "/api/conversations",
        json={"mode": "direct", "teacher_id": teacher_id, "content": "看这张图", "image_ids": [image_id]},
    )
    assert response.status_code == 200

    # 上传者本人可以访问
    assert client.get(f"/api/media/{image_id}").status_code == 200

    # 会话中的教师可以访问
    login(client, "teacher", "tea-media", "李老师")
    assert client.get(f"/api/media/{image_id}").status_code == 200

    # 无关学生 B 被拒绝（此前漏洞：任何登录用户可遍历 image_id）
    login(client, "student", "stu-media-b", "学生乙", class_id=["高一2班"])
    assert client.get(f"/api/media/{image_id}").status_code == 403

    # 无关教师同样被拒绝
    login(client, "teacher", "tea-media-other", "王老师")
    assert client.get(f"/api/media/{image_id}").status_code == 403

    # 管理员可以访问
    login(client, "admin", "adm-media", "管理员")
    assert client.get(f"/api/media/{image_id}").status_code == 200


def test_unattached_image_only_visible_to_owner_and_admin(client: TestClient):
    # 学生 A 上传但尚未附加到任何消息
    login(client, "student", "stu-media-c", "学生丙", class_id=["高一1班"])
    image_id = _upload_image(client)
    assert client.get(f"/api/media/{image_id}").status_code == 200

    # 其他人（包括教师）不可见
    login(client, "teacher", "tea-media-d", "赵老师")
    assert client.get(f"/api/media/{image_id}").status_code == 403

    login(client, "student", "stu-media-e", "学生丁", class_id=["高一1班"])
    assert client.get(f"/api/media/{image_id}").status_code == 403

    login(client, "admin", "adm-media-2", "管理员二")
    assert client.get(f"/api/media/{image_id}").status_code == 200
