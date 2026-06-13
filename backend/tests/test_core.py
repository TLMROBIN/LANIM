import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    app = create_app(database_url="sqlite:///:memory:", media_dir=tmp_path / "media")
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


def test_oidc_claims_create_profile_and_missing_role_is_rejected(client: TestClient):
    response = login(
        client,
        "student",
        "stu-001",
        "张三",
        class_id=["高一1班"],
        grade=["高一"],
    )
    assert response.json()["user"]["display_name"] == "张三"

    me = client.get("/api/me")
    assert me.status_code == 200
    assert me.json()["role"] == "student"
    assert me.json()["class_id"] == "高一1班"

    bad = client.post(
        "/api/dev/login",
        json={"sub": "broken", "preferred_username": "broken", "name": "无角色"},
    )
    assert bad.status_code == 400
    assert "role" in bad.json()["detail"]


def test_student_cannot_access_teacher_or_admin_apis(client: TestClient):
    login(client, "student", "stu-001", "张三")

    assert client.get("/api/teacher/inbox").status_code == 403
    assert client.get("/api/admin/feishu/status").status_code == 403


def test_student_sends_direct_text_and_image_to_teacher(client: TestClient):
    login(client, "teacher", "tea-001", "李老师")
    teacher_id = client.get("/api/me").json()["id"]
    login(client, "admin", "admin-001", "管理员")
    client.put(
        f"/api/admin/teachers/{teacher_id}",
        json={"enabled": True, "feishu_open_id": "ou_teacher"},
    )

    login(client, "student", "stu-001", "张三", class_id=["高一1班"])
    upload = client.post(
        "/api/uploads/images",
        files={"file": ("question.png", io.BytesIO(b"fake-image"), "image/png")},
    )
    assert upload.status_code == 200
    image_id = upload.json()["id"]

    created = client.post(
        "/api/conversations",
        json={
            "mode": "direct",
            "teacher_id": teacher_id,
            "subject": "物理",
            "content": "这道题为什么选 B？",
            "image_ids": [image_id],
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["teacher_id"] == teacher_id
    assert body["student_name"] == "张三"

    inbox = client.get("/api/teacher/inbox")
    assert inbox.status_code == 403

    login(client, "teacher", "tea-001", "李老师")
    inbox = client.get("/api/teacher/inbox")
    assert inbox.status_code == 200
    assert inbox.json()[0]["last_message"]["content"] == "这道题为什么选 B？"
    assert inbox.json()[0]["unread_count"] == 1


def test_route_mode_resolves_class_subject_teacher_and_missing_route_errors(client: TestClient):
    login(client, "teacher", "tea-physics", "王老师")
    teacher_id = client.get("/api/me").json()["id"]
    login(client, "admin", "admin-001", "管理员")
    client.put(f"/api/admin/teachers/{teacher_id}", json={"enabled": True})
    route = client.post(
        "/api/admin/routes",
        json={"class_id": "高一1班", "subject": "物理", "teacher_id": teacher_id},
    )
    assert route.status_code == 200

    login(client, "student", "stu-001", "张三", class_id=["高一1班"])
    created = client.post(
        "/api/conversations",
        json={"mode": "route", "subject": "物理", "content": "请问受力分析怎么做？"},
    )
    assert created.status_code == 200
    assert created.json()["teacher_id"] == teacher_id

    missing = client.post(
        "/api/conversations",
        json={"mode": "route", "subject": "化学", "content": "有人吗？"},
    )
    assert missing.status_code == 404
    assert "未配置" in missing.json()["detail"]


def test_feishu_reply_maps_back_to_conversation(client: TestClient):
    login(client, "teacher", "tea-001", "李老师")
    teacher_id = client.get("/api/me").json()["id"]
    login(client, "admin", "admin-001", "管理员")
    client.put(
        f"/api/admin/teachers/{teacher_id}",
        json={"enabled": True, "feishu_open_id": "ou_teacher"},
    )

    login(client, "student", "stu-001", "张三", class_id=["高一1班"])
    created = client.post(
        "/api/conversations",
        json={
            "mode": "direct",
            "teacher_id": teacher_id,
            "subject": "物理",
            "content": "飞书能回复吗？",
        },
    )
    conversation_id = created.json()["id"]

    login(client, "admin", "admin-001", "管理员")
    deliveries = client.get("/api/admin/feishu/status").json()["deliveries"]
    assert deliveries[0]["conversation_id"] == conversation_id
    assert deliveries[0]["status"] == "queued"

    event = client.post(
        "/api/dev/feishu/reply",
        json={
            "reply_to_message_id": deliveries[0]["feishu_message_id"],
            "sender_open_id": "ou_teacher",
            "content": "可以，这里是飞书回复。",
        },
    )
    assert event.status_code == 200

    messages = client.get(f"/api/conversations/{conversation_id}/messages")
    assert [m["content"] for m in messages.json()] == ["飞书能回复吗？", "可以，这里是飞书回复。"]
    assert messages.json()[1]["source"] == "feishu"
