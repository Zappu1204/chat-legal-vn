"""End-to-end backend tests against SQLite + fakes (no live infra)."""

from __future__ import annotations

import os

import httpx
import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client() -> AsyncClient:
    from app.main import create_app

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def _register(client: AsyncClient, email: str) -> dict:
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "UserPass!123", "full_name": "Test"},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_register_login_me(client: AsyncClient) -> None:
    tokens = await _register(client, "user1@example.com")
    access = tokens["access_token"]
    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 200
    assert me.json()["email"] == "user1@example.com"
    assert me.json()["role"] == "user"


async def test_rbac_admin_only(client: AsyncClient) -> None:
    tokens = await _register(client, "user2@example.com")
    access = tokens["access_token"]
    # A normal user must NOT reach the admin config endpoint.
    r = await client.get(
        "/api/v1/admin/config", headers={"Authorization": f"Bearer {access}"}
    )
    assert r.status_code == 403


async def _admin_login(client: AsyncClient) -> str:
    r = await client.post(
        "/api/v1/auth/login",
        json={
            "email": os.environ["CLVN_BOOTSTRAP_ADMIN_EMAIL"],
            "password": os.environ["CLVN_BOOTSTRAP_ADMIN_PASSWORD"],
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


async def test_admin_can_read_and_update_config(client: AsyncClient) -> None:
    admin = await _admin_login(client)
    headers = {"Authorization": f"Bearer {admin}"}

    r = await client.get("/api/v1/admin/config", headers=headers)
    assert r.status_code == 200
    rev0 = r.json()["revision"]

    # Update a tunable + a secret; secret must never echo back.
    r = await client.put(
        "/api/v1/admin/config",
        headers=headers,
        json={"reranker_top_k": 8, "llm_api_key": "super-secret"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["revision"] == rev0 + 1
    assert body["values"]["reranker_top_k"] == 8
    assert body["secrets_set"]["llm_api_key"] is True
    assert "super-secret" not in r.text


async def test_chat_stream_emits_sources_then_text(client: AsyncClient) -> None:
    tokens = await _register(client, "chatter@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    async with client.stream(
        "POST",
        "/api/v1/chat/stream",
        headers=headers,
        json={"query": "Điểm h khoản 2 Điều 7 Nghị định 168/2024/NĐ-CP nói gì?"},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        raw = ""
        async for chunk in resp.aiter_text():
            raw += chunk

    # The stream must contain sources before the done event, and text deltas.
    assert "event: meta" in raw
    assert "event: sources" in raw
    assert "event: text-delta" in raw
    assert "event: citations" in raw
    assert "event: done" in raw
    # Sources must appear before the done terminal event.
    assert raw.index("event: sources") < raw.index("event: done")
    # The seeded source card should reference the decree number.
    assert "168/2024" in raw


async def test_chat_persists_conversation(client: AsyncClient) -> None:
    tokens = await _register(client, "hist@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    async with client.stream(
        "POST",
        "/api/v1/chat/stream",
        headers=headers,
        json={"query": "vượt đèn đỏ phạt bao nhiêu?"},
    ) as resp:
        async for _ in resp.aiter_text():
            pass

    convos = await client.get("/api/v1/conversations", headers=headers)
    assert convos.status_code == 200
    items = convos.json()
    assert len(items) == 1
    detail = await client.get(
        f"/api/v1/conversations/{items[0]['id']}", headers=headers
    )
    assert detail.status_code == 200
    roles = [m["role"] for m in detail.json()["messages"]]
    assert "user" in roles and "assistant" in roles
