"""Unit test cho production ModelClient — KHÔNG gọi API thật (mock transport).

Bao phủ hai tầng lỗi TÁCH BIỆT (Bước refactor fail-closed §2026-08-07):
1. Transport retry: lỗi TẠM THỜI (timeout/429/5xx/mất kết nối) được retry bounded; lỗi
   CẤU HÌNH/XÁC THỰC (401/403/400/404/422/409) fail NGAY, không retry.
2. Schema repair: JSON hỏng/sai hình thức → sửa ĐÚNG MỘT lần; field thừa vô hại → bỏ
   qua không cần sửa.
Và bất biến: exception KHÔNG thuộc openai.APIError (bug lập trình thật) PHẢI propagate
nguyên vẹn — không bị nuốt và giả vờ là lỗi model.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest

from src.nlu.model_client import ModelClientError, ModelResponse, OpenAICompatibleModelClient

_REQ = httpx.Request("POST", "https://example.invalid/v1/chat/completions")


def _make_client(**kw: Any) -> OpenAICompatibleModelClient:
    # Key giả — __init__ chỉ tạo object OpenAI, không gọi mạng. Ta thay _client bằng fake.
    kw.setdefault("retry_backoff_seconds", 0.0)  # test nhanh, không chờ backoff thật
    return OpenAICompatibleModelClient(api_key="dummy", model="test-model", base_url="https://example.invalid", **kw)


def _fake_completion(content: str | None, *, model: str = "test-model") -> Any:
    usage = SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18)
    msg = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(choices=[choice], usage=usage, model=model, id="req-123")


def _status_exc(cls: type, status: int, message: str = "boom") -> Exception:
    return cls(message, response=httpx.Response(status, request=_REQ), body=None)


class _FakeCompletions:
    def __init__(self, behavior: Any) -> None:
        self._behavior = behavior

    def create(self, **kwargs: Any) -> Any:
        if callable(self._behavior):
            return self._behavior(**kwargs)
        return self._behavior


def _install(client: OpenAICompatibleModelClient, behavior: Any) -> None:
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions(behavior)))


def _sequenced(*items: Any):  # type: ignore[no-untyped-def]
    """Trả/raise lần lượt mỗi item cho từng lời gọi create() (mô phỏng nhiều attempt)."""
    seq = iter(items)

    def _behavior(**kwargs: Any) -> Any:
        item = next(seq)
        if isinstance(item, Exception):
            raise item
        return item

    return _behavior


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
def test_valid_json_records_usage_and_latency() -> None:
    client = _make_client()
    payload = {"utterance_type": "DEVICE_COMMAND", "intent": "turn_on", "target_device_ids": ["den_bep"]}
    _install(client, _fake_completion(json.dumps(payload)))
    out = client.understand(utterance="bật đèn bếp", context={"rooms": [], "devices": []})
    assert out["intent"] == "turn_on"
    assert len(client.calls) == 1
    rec = client.calls[0]
    assert isinstance(rec, ModelResponse)
    assert rec.total_tokens == 18
    assert rec.latency_ms >= 0
    assert rec.request_id == "req-123"
    assert client.total_tokens == 18


def test_prompt_cache_key_is_stable_and_usage_details_are_recorded() -> None:
    requests: list[dict[str, Any]] = []

    def complete(**kwargs: Any) -> Any:
        requests.append(kwargs)
        response = _fake_completion(json.dumps({"utterance_type": "SOCIAL_UTTERANCE", "intent": "confirm"}))
        response.usage.prompt_tokens_details = SimpleNamespace(cached_tokens=1024, cache_write_tokens=0)
        response.usage.completion_tokens_details = SimpleNamespace(reasoning_tokens=9)
        return response

    client = _make_client()
    _install(client, complete)
    client.understand(utterance="xin chào", context={})
    client.understand(utterance="chào buổi tối", context={})

    assert requests[0]["prompt_cache_key"] == requests[1]["prompt_cache_key"]
    assert requests[0]["prompt_cache_key"].startswith("smarthome-")
    assert client.calls[0].cached_tokens == 1024
    assert client.calls[0].cache_write_tokens == 0
    assert client.calls[0].reasoning_tokens == 9
    assert client.calls[0].prompt_bytes > 0


def test_empty_content_returns_malformed_sentinel() -> None:
    client = _make_client()
    _install(client, _fake_completion(""))
    out = client.understand(utterance="x", context={})
    assert out.get("__malformed__") is True
    assert len(client.calls) == 1  # vẫn ghi lại lời gọi


def test_non_json_content_returns_malformed_sentinel() -> None:
    client = _make_client()
    _install(client, _fake_completion("đây không phải JSON"))
    out = client.understand(utterance="x", context={})
    assert out.get("__malformed__") is True


def test_reset_clears_calls() -> None:
    client = _make_client()
    _install(client, _fake_completion(json.dumps({"utterance_type": "SOCIAL_UTTERANCE", "intent": "confirm"})))
    client.understand(utterance="ok", context={})
    assert client.calls
    client.reset()
    assert client.calls == []


def test_model_client_not_in_graph_state() -> None:
    from src.agent.state import AgentState

    assert "model_client" not in AgentState.__annotations__


# ---------------------------------------------------------------------------
# Transport retry: CHỈ lỗi tạm thời được retry, bounded.
# ---------------------------------------------------------------------------
def test_transient_error_is_retried_then_succeeds() -> None:
    """Timeout lần đầu, thành công lần hai → trả kết quả, KHÔNG raise."""
    client = _make_client(max_transport_retries=2)
    good = _fake_completion(json.dumps({"utterance_type": "DEVICE_COMMAND", "intent": "turn_on"}))
    _install(client, _sequenced(openai.APITimeoutError(request=_REQ), good))
    out = client.understand(utterance="x", context={})
    assert out["intent"] == "turn_on"
    assert len(client.calls) == 2, "1 lần lỗi + 1 lần thành công"
    assert client.calls[0].provider_error_type == "APITimeoutError"
    assert client.calls[1].provider_error_type is None


@pytest.mark.parametrize(
    "make_exc",
    [
        lambda: openai.APITimeoutError(request=_REQ),
        lambda: openai.APIConnectionError(request=_REQ),
        lambda: _status_exc(openai.RateLimitError, 429),
        lambda: _status_exc(openai.InternalServerError, 500),
    ],
)
def test_transient_errors_retried_until_exhausted_then_abstain(make_exc: Any) -> None:
    """Lỗi tạm thời (timeout/connection/429/5xx) dai dẳng → retry hết bounded rồi mới abstain."""
    client = _make_client(max_transport_retries=2)  # tối đa 3 attempt
    _install(client, lambda **kw: (_ for _ in ()).throw(make_exc()))
    with pytest.raises(ModelClientError):
        client.understand(utterance="x", context={})
    assert len(client.calls) == 3, "phải thử đủ 1 + 2 lần retry trước khi bỏ cuộc"
    assert all(c.provider_error_type is not None for c in client.calls)


@pytest.mark.parametrize(
    "make_exc",
    [
        lambda: _status_exc(openai.AuthenticationError, 401),
        lambda: _status_exc(openai.PermissionDeniedError, 403),
        lambda: _status_exc(openai.BadRequestError, 400),
        lambda: _status_exc(openai.NotFoundError, 404),
        lambda: _status_exc(openai.UnprocessableEntityError, 422),
    ],
)
def test_non_retryable_errors_fail_immediately_no_retry(make_exc: Any) -> None:
    """Lỗi cấu hình/xác thực (401/403/400/404/422) fail NGAY — retry không sửa được sai key."""
    client = _make_client(max_transport_retries=2)
    _install(client, lambda **kw: (_ for _ in ()).throw(make_exc()))
    with pytest.raises(ModelClientError):
        client.understand(utterance="x", context={})
    assert len(client.calls) == 1, "không được retry lỗi cấu hình/xác thực"


def test_unrecognized_api_error_fails_immediately() -> None:
    """Lỗi API openai chưa được liệt kê (không transient, không non-retryable đã biết) →
    mặc định AN TOÀN là không retry mù quáng, fail ngay."""
    client = _make_client(max_transport_retries=2)
    _install(client, lambda **kw: (_ for _ in ()).throw(_status_exc(openai.ConflictError, 409)))
    with pytest.raises(ModelClientError):
        client.understand(utterance="x", context={})
    assert len(client.calls) == 1


def test_unexpected_non_api_exception_propagates_uncaught() -> None:
    """Exception KHÔNG PHẢI openai.APIError (bug lập trình thật, vd sai kiểu tham số) PHẢI
    propagate nguyên vẹn — KHÔNG được bắt và giả vờ là lỗi model."""
    client = _make_client()

    def boom(**kwargs: Any) -> Any:
        raise TypeError("bug lập trình giả lập")

    _install(client, boom)
    with pytest.raises(TypeError):
        client.understand(utterance="x", context={})
    assert len(client.calls) == 0, "không có ModelResponse nào được ghi cho một exception không phân loại được"


def test_unsupported_optional_parameter_is_omitted_without_changing_model() -> None:
    """Capability negotiation is driven by the provider error, not a model allowlist."""
    client = OpenAICompatibleModelClient(
        api_key="dummy",
        model="parameter-compatibility-model",
        base_url="https://parameter-compatibility.invalid",
        temperature=0.5,
        retry_backoff_seconds=0.0,
    )
    requests: list[dict[str, Any]] = []

    def reject_temperature_once(**kwargs: Any) -> Any:
        requests.append(kwargs)
        if "temperature" in kwargs:
            raise openai.BadRequestError(
                "unsupported optional parameter",
                response=httpx.Response(400, request=_REQ),
                body={"param": "temperature", "code": "unsupported_value"},
            )
        return _fake_completion(
            json.dumps({"utterance_type": "SOCIAL_UTTERANCE", "intent": "confirm"}),
            model=kwargs["model"],
        )

    _install(client, reject_temperature_once)
    client.understand(utterance="ok", context={})

    assert len(requests) == 2
    assert requests[0]["temperature"] == 0.5
    assert "temperature" not in requests[1]
    assert {request["model"] for request in requests} == {"parameter-compatibility-model"}


# ---------------------------------------------------------------------------
# Schema repair: ĐÚNG MỘT lần, tách biệt khỏi transport retry.
# ---------------------------------------------------------------------------
def test_structured_generate_ignores_harmless_extra_field() -> None:
    """LLM hiểu đúng nhưng thêm field lạ ('explanation') → bỏ qua, KHÔNG cần repair."""
    from src.nlu.schemas import SemanticGoal

    client = _make_client()
    good_with_extra = {
        "raw_utterance": "bật đèn bếp", "goal_description": "bật đèn bếp", "action_hint": "turn_on",
        "utterance_type": "DEVICE_COMMAND", "confidence": 0.9, "target_device_ids": ["den_bep"],
        "explanation": "field thừa LLM tự thêm",  # không có trong schema
    }
    _install(client, _fake_completion(json.dumps(good_with_extra)))
    goal = client.structured_generate_sync("bật đèn bếp", SemanticGoal, context={"utterance": "bật đèn bếp"})
    assert goal.action_hint == "turn_on"
    assert goal.target_device_ids == ["den_bep"]
    assert len(client.calls) == 1, "field thừa vô hại không được tốn thêm một lượt gọi"


def test_structured_generate_repairs_wrong_shape_once() -> None:
    """Hiểu đúng nhưng SAI hình thức (thiếu field bắt buộc) → repair-once rồi thành công."""
    from src.nlu.schemas import SemanticGoal

    client = _make_client()
    bad = json.dumps({"goal_description": "bật đèn bếp"})  # thiếu raw_utterance/confidence/utterance_type
    good = json.dumps({
        "raw_utterance": "bật đèn bếp", "goal_description": "bật đèn bếp", "action_hint": "turn_on",
        "utterance_type": "DEVICE_COMMAND", "confidence": 0.9, "target_device_ids": ["den_bep"],
    })
    _install(client, _sequenced(_fake_completion(bad), _fake_completion(good)))
    goal = client.structured_generate_sync("bật đèn bếp", SemanticGoal, context={"utterance": "bật đèn bếp"})
    assert goal.target_device_ids == ["den_bep"]
    assert len(client.calls) == 2, "phải gọi lại đúng một lần để sửa hình thức"


def test_structured_generate_abstains_after_repair_fails() -> None:
    """Sai hình thức cả hai lần → abstain (ModelClientError), KHÔNG đoán bừa, KHÔNG có lần sửa thứ ba."""
    from src.nlu.schemas import SemanticGoal

    client = _make_client()
    bad = json.dumps({"goal_description": "x"})
    _install(client, _sequenced(_fake_completion(bad), _fake_completion(bad)))
    with pytest.raises(ModelClientError):
        client.structured_generate_sync("x", SemanticGoal, context={"utterance": "x"})
    assert len(client.calls) == 2, "đúng 1 lần gốc + 1 lần sửa, không hơn"
    assert client.calls[-1].provider_error_type == "SchemaRepairFailed"


def test_malformed_content_repaired_once_then_succeeds() -> None:
    """Nội dung rỗng/không phải JSON cũng đi qua đúng cơ chế repair-once (không phải
    lỗi transport, KHÔNG được retry ở tầng transport)."""
    from src.nlu.schemas import SemanticGoal

    client = _make_client()
    good = json.dumps({
        "raw_utterance": "x", "goal_description": "x", "utterance_type": "DEVICE_COMMAND", "confidence": 0.9,
    })
    _install(client, _sequenced(_fake_completion(""), _fake_completion(good)))
    goal = client.structured_generate_sync("x", SemanticGoal, context={"utterance": "x"})
    assert goal.raw_utterance == "x"
    assert len(client.calls) == 2


def test_transport_error_during_repair_round_is_not_a_second_repair() -> None:
    """Lỗi TRANSPORT xảy ra ở lượt sửa (không phải lỗi hình thức) → vẫn là lỗi transport,
    propagate ModelClientError ngay, KHÔNG tính là 'lần sửa thứ hai'."""
    from src.nlu.schemas import SemanticGoal

    client = _make_client(max_transport_retries=0)  # fail ngay lần lỗi transport thứ nhất
    bad = json.dumps({"goal_description": "x"})
    _install(client, _sequenced(_fake_completion(bad), _status_exc(openai.AuthenticationError, 401)))
    with pytest.raises(ModelClientError):
        client.structured_generate_sync("x", SemanticGoal, context={"utterance": "x"})
    assert len(client.calls) == 2


# ---------------------------------------------------------------------------
# Strict Structured Outputs: thử MỘT lần cho mỗi (model, schema) rồi nhớ kết quả.
#
# Bối cảnh: schema có field free-form (dict[str, Any]) bị strict mode của OpenAI từ chối
# ("additionalProperties is required to be false") → 400 ở MỌI lời gọi nếu không ghi nhớ.
# ---------------------------------------------------------------------------
class _FakeParse:
    def __init__(self, behavior: Any) -> None:
        self._behavior = behavior
        self.count = 0

    def parse(self, **kwargs: Any) -> Any:
        self.count += 1
        if isinstance(self._behavior, Exception):
            raise self._behavior
        return self._behavior


def _install_with_beta(client: OpenAICompatibleModelClient, parse_behavior: Any, create_behavior: Any) -> _FakeParse:
    """Gắn cả đường strict (beta.chat.completions.parse) lẫn đường fallback (chat.completions.create)."""
    fake_parse = _FakeParse(parse_behavior)
    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=_FakeCompletions(create_behavior)),
        beta=SimpleNamespace(chat=SimpleNamespace(completions=fake_parse)),
    )
    return fake_parse


@pytest.fixture(autouse=True)
def _clear_strict_cache() -> Any:
    """Cache strict-unsupported ở phạm vi module → phải dọn để test không rò sang nhau."""
    from src.nlu.model_client import _STRICT_SCHEMA_UNSUPPORTED

    _STRICT_SCHEMA_UNSUPPORTED.clear()
    yield
    _STRICT_SCHEMA_UNSUPPORTED.clear()


def test_strict_schema_rejection_is_remembered_and_not_retried() -> None:
    """Endpoint từ chối schema ở strict mode → fallback, và KHÔNG tốn round-trip 400 lần nữa."""
    from src.nlu.schemas import SemanticGoal

    client = _make_client()
    good = json.dumps({
        "raw_utterance": "bật đèn bếp", "goal_description": "bật đèn bếp", "action_hint": "turn_on",
        "utterance_type": "DEVICE_COMMAND", "confidence": 0.9, "target_device_ids": ["den_bep"],
    })
    fake_parse = _install_with_beta(
        client,
        _status_exc(openai.BadRequestError, 400, "Invalid schema for response_format"),
        _fake_completion(good),
    )

    for _ in range(3):
        goal = client.structured_generate_sync("bật đèn bếp", SemanticGoal, context={"utterance": "bật đèn bếp"})
        assert goal.action_hint == "turn_on", "fallback vẫn phải cho kết quả đúng"

    assert fake_parse.count == 1, "chỉ được trả giá 400 đúng một lần, các lượt sau đi thẳng fallback"
    assert len(client.calls) == 3, "mỗi lượt chỉ còn một round-trip thật, không có lượt sửa schema"
    assert client.schema_repairs == 0


def test_transient_error_in_strict_path_is_not_swallowed() -> None:
    """Lỗi TẠM THỜI ở đường strict phải propagate cho tầng transport-retry, KHÔNG âm thầm
    rơi xuống fallback — nuốt ở đây là nhân đôi thời gian chờ cho cùng một sự cố mạng."""
    from src.nlu.schemas import SemanticGoal

    client = _make_client(max_transport_retries=0)  # fail ngay, không backoff
    fake_parse = _install_with_beta(client, openai.APITimeoutError(request=_REQ), _fake_completion("{}"))

    with pytest.raises(ModelClientError, match="APITimeoutError"):
        client.structured_generate_sync("x", SemanticGoal, context={"utterance": "x"})

    assert fake_parse.count == 1
    assert [c.provider_error_type for c in client.calls] == ["APITimeoutError"]
    from src.nlu.model_client import _STRICT_SCHEMA_UNSUPPORTED

    assert not _STRICT_SCHEMA_UNSUPPORTED, "timeout KHÔNG phải bằng chứng schema bị từ chối"
