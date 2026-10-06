"""Test endpoint tích hợp NLU: POST /api/v1/agent/nlu.

Không gọi API thật (conftest đặt LLM_DISABLED=true). Đường LLM được test bằng fake
client tiêm qua monkeypatch. Bảo đảm: không execute thiết bị, luôn requires_policy_validation,
registry lấy từ Backend, provider error → controlled response, không lộ key.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.services import nlu_gateway
from src.services.nlu_gateway import NluRequest, analyze


@pytest.mark.asyncio
async def test_direct_command_returns_candidate_plan(client, login) -> None:
    headers = await login("bo")
    r = await client.post("/api/v1/agent/nlu", json={"message": "tắt đèn phòng khách"}, headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["type"] == "candidate_plan"
    assert body["execution_status"] == "not_executed"
    assert body["candidate_plan"]["requires_policy_validation"] is True
    assert body["candidate_plan"]["actions"][0]["action"] == "turn_off"
    assert body["metadata"]["route"] == "deterministic"


@pytest.mark.asyncio
async def test_clarification_returns_options_and_no_action(client, login) -> None:
    headers = await login("bo")
    r = await client.post("/api/v1/agent/nlu", json={"message": "bật đèn ngủ"}, headers=headers)
    body = r.json()
    assert body["type"] == "clarification"
    assert body["requires_clarification"] is True
    assert body["candidate_plan"] is None
    assert body["execution_status"] == "not_executed"


@pytest.mark.asyncio
async def test_registry_from_backend_not_request(client, login) -> None:
    """Catalog bịa trong request bị bỏ qua; grounding vẫn dùng Registry thật."""
    headers = await login("bo")
    r = await client.post(
        "/api/v1/agent/nlu",
        json={"message": "bật đèn bếp", "device_catalog": [{"device_id": "fake_device", "name": "ma"}]},
        headers=headers,
    )
    body = r.json()
    assert body["type"] == "candidate_plan"
    ids = [a["device_id"] for a in body["candidate_plan"]["actions"]]
    assert "fake_device" not in ids
    assert ids == ["den_bep"]


@pytest.mark.asyncio
async def test_requires_auth(client) -> None:
    r = await client.post("/api/v1/agent/nlu", json={"message": "tắt đèn phòng khách"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_slot_filling_over_two_turns(client, login) -> None:
    """Pending Intent Store: lượt 1 hỏi lại, lượt 2 (cùng session) hoàn tất plan."""
    headers = await login("bo")
    sid = "sess-slotfill-1"
    r1 = await client.post(
        "/api/v1/agent/nlu", json={"message": "bật lên đi", "session_id": sid}, headers=headers
    )
    b1 = r1.json()
    assert b1["type"] == "clarification"
    r2 = await client.post(
        "/api/v1/agent/nlu", json={"message": "đèn bếp", "session_id": sid}, headers=headers
    )
    b2 = r2.json()
    assert b2["type"] == "candidate_plan"
    assert [a["device_id"] for a in b2["candidate_plan"]["actions"]] == ["den_bep"]
    assert b2["execution_status"] == "not_executed"


@pytest.mark.asyncio
async def test_slot_filling_isolated_by_session(client, login) -> None:
    """Câu trả lời ở session khác KHÔNG nối nhầm vào pending của session này."""
    headers = await login("bo")
    await client.post("/api/v1/agent/nlu", json={"message": "bật lên đi", "session_id": "sA"}, headers=headers)
    r = await client.post("/api/v1/agent/nlu", json={"message": "đèn bếp", "session_id": "sB"}, headers=headers)
    body = r.json()
    # sB không có pending → "đèn bếp" (danh từ trần, không động từ) phải hỏi lại, không tự bật.
    assert body["type"] in {"clarification", "controlled_error"}
    assert body["candidate_plan"] is None


@pytest.mark.asyncio
async def test_reference_resolution_uses_last_device(client, login) -> None:
    headers = await login("bo")
    r = await client.post(
        "/api/v1/agent/nlu",
        json={"message": "tắt nó đi", "last_device_id": "tv_phong_khach"},
        headers=headers,
    )
    body = r.json()
    assert body["type"] == "candidate_plan"
    assert body["last_device_id"] == "tv_phong_khach"


@pytest.mark.asyncio
async def test_no_key_in_response(client, login) -> None:
    headers = await login("bo")
    r = await client.post("/api/v1/agent/nlu", json={"message": "làm chỗ này dễ chịu hơn"}, headers=headers)
    text = r.text.lower()
    assert "api_key" not in text
    assert "authorization" not in text
    assert "sk-" not in text


@pytest.mark.asyncio
async def test_logged_in_user_rooms_ground_private_and_home_phrases_differently(client, login) -> None:
    """Route thật phải lấy room assignment của CurrentUser, không suy lại chỉ từ role."""
    headers = await login("bo")
    rooms = (await client.get("/api/v1/rooms", headers=headers)).json()
    parents = next(r for r in rooms if r["name"] == "Phòng ngủ bố mẹ")
    kids = next(r for r in rooms if r["name"] == "Phòng ngủ con")
    members = (await client.get("/api/v1/members", headers=headers)).json()
    bo = next(m for m in members if m["username"] == "bo")
    updated = await client.patch(
        f"/api/v1/members/{bo['id']}",
        headers=headers,
        json={"home_room_id": parents["id"], "private_room_id": kids["id"]},
    )
    assert updated.status_code == 200, updated.text

    owned = await client.post(
        "/api/v1/agent/nlu",
        headers=headers,
        json={"message": "bật đèn phòng ngủ của tôi", "session_id": "assigned-private"},
    )
    generic = await client.post(
        "/api/v1/agent/nlu",
        headers=headers,
        json={"message": "bật đèn phòng ngủ", "session_id": "assigned-home"},
    )

    assert owned.status_code == generic.status_code == 200
    assert {a["device_id"] for a in owned.json()["candidate_plan"]["actions"]} == {
        "den_ngu_con", "den_ban_hoc",
    }
    assert {a["device_id"] for a in generic.json()["candidate_plan"]["actions"]} == {
        "den_ngu_bo_me", "den_ban_lam_viec",
    }


# --------------------------------------------------------------------------
# Đường LLM bằng fake client (không mạng) — inject + fail-safe
# --------------------------------------------------------------------------
class _FakeGoodClient:
    """Model thật giả lập: implement structured_generate (giao diện pipeline open-ended)."""

    provider = "openai"
    model = "test-runtime-model"

    def __init__(self) -> None:
        self.calls: list[Any] = []
        self.generate_called = False

    def reset(self) -> None:
        self.calls = []

    @property
    def total_tokens(self) -> int:
        return 42

    def structured_generate_sync(self, prompt: str, schema: Any, **k: Any) -> Any:
        from types import SimpleNamespace

        self.generate_called = True
        self.calls.append(SimpleNamespace(model="test-runtime-model", total_tokens=42, latency_ms=1.0, provider_error_type=None))
        from src.nlu.schemas import SemanticGoal

        if schema is SemanticGoal:
            return SemanticGoal(
                intent="làm dễ chịu", goal_description="làm chỗ này dễ chịu hơn", action_hint="turn_on",
                utterance_type="DEVICE_COMMAND", raw_utterance=prompt, confidence=0.9,
                target_device_ids=["den_bep"],
            )
        return schema.model_construct()

    async def structured_generate(self, prompt: str, schema: Any, **k: Any) -> Any:
        return self.structured_generate_sync(prompt, schema, **k)


class _FakeErrorClient:
    provider = "openai"
    model = "test-runtime-model"

    def __init__(self) -> None:
        self.calls: list[Any] = []

    def reset(self) -> None:
        self.calls = []

    @property
    def total_tokens(self) -> int:
        return 0

    def structured_generate_sync(self, prompt: str, schema: Any, **k: Any) -> Any:
        from src.nlu.model_client import ModelClientError

        raise ModelClientError("AuthenticationError")

    async def structured_generate(self, prompt: str, schema: Any, **k: Any) -> Any:
        return self.structured_generate_sync(prompt, schema, **k)


@pytest.mark.asyncio
async def test_llm_client_injected_and_used(monkeypatch) -> None:
    fake = _FakeGoodClient()
    monkeypatch.setattr(nlu_gateway, "build_nlu_model_client", lambda: fake)
    # câu paraphrase không keyword → deterministic UNKNOWN → dùng model
    resp = await analyze(NluRequest(message="làm chỗ này dễ chịu hơn một chút"))
    assert fake.generate_called is True
    assert resp.metadata.route == "llm"
    assert resp.metadata.model == "test-runtime-model"
    assert resp.execution_status == "not_executed"


@pytest.mark.asyncio
async def test_provider_error_returns_controlled_response(monkeypatch) -> None:
    monkeypatch.setattr(nlu_gateway, "build_nlu_model_client", lambda: _FakeErrorClient())
    resp = await analyze(NluRequest(message="làm chỗ này dễ chịu hơn một chút"))
    assert resp.type == "controlled_error"
    assert resp.error is not None and resp.error.code == "NLU_MODEL_UNAVAILABLE"
    assert resp.candidate_plan is None
    assert resp.execution_status == "not_executed"
