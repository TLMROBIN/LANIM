from __future__ import annotations

import argparse
import asyncio
from typing import Any

import httpx

from .config import Settings
from .db import Base, build_sessionmaker
from .schemas import AdminUserCreate
from .services import upsert_admin_user


def first_attr(attributes: dict[str, Any], key: str) -> str | None:
    value = attributes.get(key)
    if isinstance(value, list):
        return str(value[0]) if value else None
    if value is None:
        return None
    return str(value)


def keycloak_user_to_admin_user(user: dict[str, Any]) -> AdminUserCreate | None:
    attributes = user.get("attributes") or {}
    role = first_attr(attributes, "role") or user.get("role")
    if role not in {"student", "teacher", "admin"}:
        return None
    username = user.get("username")
    if not username:
        return None
    display_name = user.get("firstName") or user.get("lastName") or user.get("email") or username
    return AdminUserCreate(
        oidc_sub=user.get("id"),
        username=username,
        display_name=display_name,
        role=role,
        class_id=first_attr(attributes, "class_id") or first_attr(attributes, "class") or first_attr(attributes, "grade"),
        grade=first_attr(attributes, "grade"),
        enabled=bool(user.get("enabled", True)),
    )


async def fetch_keycloak_users(base_url: str, realm: str, admin_user: str, admin_password: str) -> list[dict[str, Any]]:
    root = base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=30.0) as client:
        token_response = await client.post(
            f"{root}/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": admin_user,
                "password": admin_password,
            },
        )
        token_response.raise_for_status()
        token = token_response.json()["access_token"]
        users: list[dict[str, Any]] = []
        first = 0
        while True:
            response = await client.get(
                f"{root}/admin/realms/{realm}/users",
                params={"first": first, "max": 100},
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            page = response.json()
            if not page:
                break
            users.extend(page)
            first += len(page)
        return users


async def sync_from_keycloak(args: argparse.Namespace) -> dict[str, int]:
    settings = Settings()
    if args.database_url:
        settings.database_url = args.database_url
    users = await fetch_keycloak_users(args.keycloak_base_url, args.realm, args.admin_user, args.admin_password)
    session_factory = build_sessionmaker(settings.database_url)
    Base.metadata.create_all(session_factory.kw["bind"])
    created = updated = skipped = 0
    with session_factory() as session:
        for raw_user in users:
            payload = keycloak_user_to_admin_user(raw_user)
            if payload is None:
                skipped += 1
                continue
            _, was_created = upsert_admin_user(session, payload)
            if was_created:
                created += 1
            else:
                updated += 1
        session.commit()
    return {"created": created, "updated": updated, "skipped": skipped}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync Keycloak school-platform users into LANIM.")
    parser.add_argument("--keycloak-base-url", default="http://10.50.159.62/auth")
    parser.add_argument("--realm", default="school-platform")
    parser.add_argument("--admin-user", required=True)
    parser.add_argument("--admin-password", required=True)
    parser.add_argument("--database-url")
    return parser.parse_args()


def main() -> None:
    result = asyncio.run(sync_from_keycloak(parse_args()))
    print(result)


if __name__ == "__main__":
    main()
