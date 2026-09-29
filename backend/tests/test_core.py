import io
import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import feishu, feishu_worker
from app.sync_keycloak_users import keycloak_user_to_admin_user
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


def test_oidc_claims_accept_uppercase_keycloak_role(client: TestClient):
    login(
        client,
        "STUDENT",
        "stu-uppercase",
        "大写角色学生",
        grade=["高一"],
    )

    me = client.get("/api/me")
    assert me.status_code == 200
    assert me.json()["role"] == "student"


def test_keycloak_sync_accepts_uppercase_role_attributes():
    payload = keycloak_user_to_admin_user(
        {
            "id": "keycloak-student-uppercase",
            "username": "20260002",
            "firstName": "李四",
            "enabled": True,
            "attributes": {
                "student_no": ["20260002"],
                "role": ["STUDENT"],
                "grade": ["2"],
            },
        }
    )

    assert payload is not None
    assert payload.role == "student"
    assert payload.username == "20260002"
    assert payload.display_name == "李四"
    assert payload.grade == "2"


def test_feishu_worker_main_is_sync_for_lark_long_connection():
    assert inspect.iscoroutinefunction(feishu_worker.main) is False


def test_student_cannot_access_teacher_or_admin_apis(client: TestClient):
    login(client, "student", "stu-001", "张三")

    assert client.get("/api/teacher/inbox").status_code == 403
    assert client.get("/api/admin/feishu/status").status_code == 403


def test_admin_can_bind_teacher_feishu_by_mobile(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    login(client, "teacher", "tea-resolve", "待绑定老师")
    teacher_id = client.get("/api/me").json()["id"]
    login(client, "admin", "admin-resolve", "管理员")

    async def fake_resolve(self, mobile: str) -> str:
        assert mobile == "13800138000"
        return "ou_resolved_teacher"

    monkeypatch.setattr(feishu.FeishuClient, "resolve_open_id_by_mobile", fake_resolve)
    response = client.post(
        f"/api/admin/teachers/{teacher_id}/feishu/resolve",
        json={"mobile": "13800138000"},
    )
    assert response.status_code == 200
    assert response.json()["feishu_open_id"] == "ou_resolved_teacher"

    users = client.get("/api/admin/users?role=teacher").json()["items"]
    teacher = next(item for item in users if item["id"] == teacher_id)
    assert teacher["teacher_profile"]["feishu_open_id"] == "ou_resolved_teacher"


def test_feishu_mobile_lookup_uses_open_id_and_mobile(monkeypatch: pytest.MonkeyPatch):
    calls = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 0, "data": {"user_list": [{"user_id": "ou_lookup"}]}}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            calls["url"] = url
            calls.update(kwargs)
            return FakeResponse()

    monkeypatch.setattr(feishu.httpx, "AsyncClient", lambda **kwargs: FakeClient())
    client = feishu.FeishuClient(SimpleNamespace(feishu_app_id="app", feishu_app_secret="secret"))

    async def fake_token():
        return "tenant-token"

    monkeypatch.setattr(client, "tenant_access_token", fake_token)
    assert asyncio.run(client.resolve_open_id_by_mobile("13800138000")) == "ou_lookup"
    assert calls["url"].endswith("/contact/v3/users/batch_get_id")
    assert calls["params"] == {"user_id_type": "open_id"}
    assert calls["json"] == {"mobiles": ["13800138000"]}


def test_student_sends_direct_text_and_image_to_teacher(client: TestClient):
    login(client, "teacher", "tea-001", "李老师")
    teacher_id = client.get("/api/me").json()["id"]
    login(client, "admin", "admin-001", "管理员")
    client.put(
        f"/api/admin/teachers/{teacher_id}",
        json={"enabled": True, "feishu_open_id": "ou_teacher"},
    )
    route = client.post(
        "/api/admin/routes",
        json={"class_id": "高一1班", "subject": "物理", "teacher_id": teacher_id},
    )
    assert route.status_code == 200

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


def test_student_can_message_only_teachers_assigned_to_their_class(client: TestClient):
    login(client, "teacher", "tea-class-a", "高一1班教师")
    class_a_teacher_id = client.get("/api/me").json()["id"]
    login(client, "teacher", "tea-class-b", "高一2班教师")
    class_b_teacher_id = client.get("/api/me").json()["id"]

    login(client, "admin", "admin-classes", "管理员")
    assert client.put(
        f"/api/admin/teachers/{class_a_teacher_id}",
        json={"enabled": True, "feishu_open_id": "ou_class_a_teacher"},
    ).status_code == 200
    assert client.put(
        f"/api/admin/teachers/{class_b_teacher_id}", json={"enabled": True}
    ).status_code == 200
    assert client.post(
        "/api/admin/routes",
        json={"class_id": "高一1班", "subject": "物理", "teacher_id": class_a_teacher_id},
    ).status_code == 200
    assert client.post(
        "/api/admin/routes",
        json={"class_id": "高一2班", "subject": "物理", "teacher_id": class_b_teacher_id},
    ).status_code == 200

    login(client, "student", "stu-class-a", "高一1班学生", class_id=["高一1班"])
    visible_teachers = client.get("/api/teachers").json()
    assert [teacher["id"] for teacher in visible_teachers] == [class_a_teacher_id]
    assert client.get("/api/subjects?class_id=高一2班").status_code == 403

    rejected = client.post(
        "/api/conversations",
        json={"mode": "direct", "teacher_id": class_b_teacher_id, "subject": "物理", "content": "越班消息"},
    )
    assert rejected.status_code == 403

    created = client.post(
        "/api/conversations",
        json={"mode": "direct", "teacher_id": class_a_teacher_id, "subject": "物理", "content": "初始消息"},
    )
    assert created.status_code == 200
    conversation_id = created.json()["id"]
    follow_up = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "接着原会话补充"},
    )
    assert follow_up.status_code == 200
    assert follow_up.json()["conversation_id"] == conversation_id

    login(client, "admin", "admin-classes", "管理员")
    deliveries = client.get("/api/admin/feishu/status").json()["deliveries"]
    assert sum(item["conversation_id"] == conversation_id for item in deliveries) == 2


def test_queued_feishu_delivery_embeds_student_image_in_rich_text(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    login(client, "teacher", "tea-image-post", "图片教师")
    teacher_id = client.get("/api/me").json()["id"]
    login(client, "admin", "admin-image-post", "管理员")
    client.put(
        f"/api/admin/teachers/{teacher_id}",
        json={"enabled": True, "feishu_open_id": "ou_image_teacher"},
    )
    client.post(
        "/api/admin/routes",
        json={"class_id": "高一1班", "subject": "物理", "teacher_id": teacher_id},
    )

    login(client, "student", "stu-image-post", "图片学生", class_id=["高一1班"])
    image = client.post(
        "/api/uploads/images",
        files={"file": ("question.png", io.BytesIO(b"fake-image"), "image/png")},
    )
    assert image.status_code == 200
    created = client.post(
        "/api/conversations",
        json={
            "mode": "direct",
            "teacher_id": teacher_id,
            "subject": "物理",
            "content": "请看图片",
            "image_ids": [image.json()["id"]],
        },
    )
    assert created.status_code == 200

    calls = {}

    async def fake_upload(self, image_data, filename, mime_type):
        calls["upload"] = (image_data, filename, mime_type)
        return "img_feishu_test"

    async def fake_send_post(self, receive_id, title, rows):
        calls["post"] = (receive_id, title, rows)
        return "om_rich_text_test"

    monkeypatch.setattr(feishu.FeishuClient, "upload_image", fake_upload)
    monkeypatch.setattr(feishu.FeishuClient, "send_post", fake_send_post)
    sent = asyncio.run(
        feishu.flush_queued_deliveries(
            client.app.state.session_factory,
            client.app.state.settings,
        )
    )

    assert sent == 1
    assert calls["upload"][1:] == ("question.png", "image/png")
    assert calls["post"][0] == "ou_image_teacher"
    assert any(row == [{"tag": "img", "image_key": "img_feishu_test"}] for row in calls["post"][2])


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
    route = client.post(
        "/api/admin/routes",
        json={"class_id": "高一1班", "subject": "物理", "teacher_id": teacher_id},
    )
    assert route.status_code == 200

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


def test_admin_can_create_edit_delete_students_and_create_teachers(client: TestClient):
    login(client, "admin", "admin-001", "管理员")

    created_student = client.post(
        "/api/admin/users",
        json={
            "oidc_sub": "kc-stu-1001",
            "username": "stu1001",
            "display_name": "赵同学",
            "role": "student",
            "class_id": "高一1班",
            "grade": "高一",
        },
    )
    assert created_student.status_code == 200
    student = created_student.json()
    assert student["display_name"] == "赵同学"
    assert student["class_id"] == "高一1班"

    edited_student = client.put(
        f"/api/admin/users/{student['id']}",
        json={"display_name": "赵同学A", "class_id": "高一2班", "grade": "高一"},
    )
    assert edited_student.status_code == 200
    assert edited_student.json()["display_name"] == "赵同学A"
    assert edited_student.json()["class_id"] == "高一2班"

    created_teacher = client.post(
        "/api/admin/users",
        json={
            "oidc_sub": "kc-tea-physics",
            "username": "teacher-physics",
            "display_name": "周老师",
            "role": "teacher",
            "feishu_open_id": "ou_zhou",
            "enabled": True,
        },
    )
    assert created_teacher.status_code == 200
    teacher = created_teacher.json()
    assert teacher["role"] == "teacher"
    assert teacher["teacher_profile"]["feishu_open_id"] == "ou_zhou"

    route = client.post(
        "/api/admin/routes",
        json={"class_id": "高一2班", "subject": "物理", "teacher_id": teacher["id"]},
    )
    assert route.status_code == 200

    users = client.get("/api/admin/users?role=student").json()["items"]
    assert [item["username"] for item in users] == ["stu1001"]

    deleted = client.delete(f"/api/admin/users/{student['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["ok"] is True
    assert client.get("/api/admin/users?role=student").json()["items"] == []


def test_admin_sync_users_upserts_sso_students_and_teachers(client: TestClient):
    login(client, "admin", "admin-001", "管理员")

    response = client.post(
        "/api/admin/users/sync",
        json={
            "users": [
                {
                    "oidc_sub": "keycloak-student-1",
                    "username": "sso-student-1",
                    "display_name": "SSO学生一",
                    "role": "student",
                    "class_id": "高二3班",
                    "grade": "高二",
                },
                {
                    "oidc_sub": "keycloak-teacher-1",
                    "username": "sso-teacher-1",
                    "display_name": "SSO教师一",
                    "role": "teacher",
                },
            ]
        },
    )
    assert response.status_code == 200
    assert response.json() == {"created": 2, "updated": 0, "skipped": 0}

    response = client.post(
        "/api/admin/users/sync",
        json={
            "users": [
                {
                    "oidc_sub": "keycloak-student-1",
                    "username": "sso-student-1",
                    "display_name": "SSO学生一-改",
                    "role": "student",
                    "class_id": "高二4班",
                    "grade": "高二",
                }
            ]
        },
    )
    assert response.status_code == 200
    assert response.json() == {"created": 0, "updated": 1, "skipped": 0}

    students = client.get("/api/admin/users?role=student").json()["items"]
    assert students[0]["display_name"] == "SSO学生一-改"
    assert students[0]["class_id"] == "高二4班"
    teachers = client.get("/api/admin/users?role=teacher").json()["items"]
    assert teachers[0]["teacher_profile"]["enabled"] is True


def test_admin_users_are_paginated(client: TestClient):
    login(client, "admin", "admin-001", "管理员")

    for index in range(5):
        response = client.post(
            "/api/admin/users",
            json={
                "oidc_sub": f"kc-stu-page-{index}",
                "username": f"stu-page-{index}",
                "display_name": f"分页学生{index}",
                "role": "student",
                "class_id": "高一1班",
                "grade": "高一",
            },
        )
        assert response.status_code == 200

    response = client.get("/api/admin/users?role=student&page=2&page_size=2")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 5
    assert body["page"] == 2
    assert body["page_size"] == 2
    assert body["pages"] == 3
    assert [item["username"] for item in body["items"]] == ["stu-page-2", "stu-page-3"]


def test_admin_users_can_be_filtered_by_class_and_grade(client: TestClient):
    login(client, "admin", "admin-001", "管理员")

    for index, class_id, grade in [
        (1, "高一1班", "高一"),
        (2, "高一2班", "高一"),
        (3, "高二1班", "高二"),
    ]:
        response = client.post(
            "/api/admin/users",
            json={
                "oidc_sub": f"kc-stu-filter-{index}",
                "username": f"stu-filter-{index}",
                "display_name": f"筛选学生{index}",
                "role": "student",
                "class_id": class_id,
                "grade": grade,
            },
        )
        assert response.status_code == 200

    by_class = client.get("/api/admin/users?role=student&class_id=高一2班").json()
    assert by_class["total"] == 1
    assert [item["username"] for item in by_class["items"]] == ["stu-filter-2"]

    by_grade = client.get("/api/admin/users?role=student&grade=高一").json()
    assert by_grade["total"] == 2
    assert [item["username"] for item in by_grade["items"]] == ["stu-filter-1", "stu-filter-2"]

    options = client.get("/api/admin/user-options").json()
    assert options["classes"] == ["高一1班", "高一2班", "高二1班"]
    assert options["grades"] == ["高一", "高二"]
