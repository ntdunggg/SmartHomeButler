"""Production ModelClient cho tầng NLU — gọi model API thật.

Dùng lại SDK ``openai`` đã có trong dependency (không thêm SDK mới). Tương thích mọi
endpoint OpenAI-compatible qua ``base_url`` (OpenAI, OpenRouter, ...). Triển khai đúng
Protocol ``ModelClient`` ở ``understanding.py`` (không tạo abstraction thứ hai).

Kiến trúc: hai tầng lỗi TÁCH BIỆT, không trộn lẫn (Bước refactor fail-closed §2026-08-07).

1. Transport retry — ``_chat_with_transport_retry``: chỉ retry lỗi TẠM THỜI của
   provider (timeout/429/5xx/mất kết nối), tối đa ``max_transport_retries`` lần, có
   backoff ngắn. Lỗi CẤU HÌNH/XÁC THỰC (401/403/400/404/422/409) fail NGAY, không retry
   — thử lại không sửa được sai key hay sai model name. Bất kỳ exception nào KHÔNG phải
   loại đã biết của SDK (bug lập trình thật sự, vd sai tham số gọi SDK) được PROPAGATE
   nguyên vẹn — không nuốt lỗi giả vờ là "model lỗi".
2. Schema repair — ở ``structured_generate_sync``: JSON hỏng hoặc sai hình thức so với
   schema (không phải lỗi transport) → sửa ĐÚNG MỘT lần kèm lỗi cụ thể, giữ nguyên ý
   hiểu, không bịa dữ liệu. Hỏng lần hai mới abstain (``ModelClientError``).

Bất biến:
- Trả về instance đã validate theo schema; KHÔNG execute thiết bị, KHÔNG tự chọn khi mơ hồ.
- Mọi lỗi không phục hồi được (transport hết retry / repair hết lượt) → raise
  ``ModelClientError`` duy nhất → tầng trên abstain (không đoán).
- Ghi lại envelope quan sát được (``ModelResponse``): model, tokens, latency, request_id,
  kể cả các lượt gọi lỗi (cho eval/observability).
- KHÔNG log API key / secret / authorization header.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from contextlib import contextmanager
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger("nlu.model_client")


class ModelResponse(BaseModel):
    """Envelope quan sát được cho mỗi lời gọi model (kể cả lượt lỗi)."""

    content: str | dict | None
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    # Token prefix phục vụ từ prompt cache của provider (OpenAI báo qua
    # usage.prompt_tokens_details.cached_tokens). None = provider không báo cáo —
    # KHÔNG suy đoán. Đây là số đo tất định để xác nhận caching thực sự chạy.
    cached_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    prompt_bytes: int | None = None
    latency_ms: float
    request_id: str | None = None
    provider_error_type: str | None = None


class ModelClientError(RuntimeError):
    """Lỗi suy luận ĐÃ BIẾT (transport hết retry hoặc schema hết lượt sửa).

    Đây là RANH GIỚI duy nhất mà tầng gọi (graph node) được phép bắt để fail-closed.
    Bug lập trình thật sự (KeyError/AttributeError/TypeError trong code của ta, hay bất
    kỳ exception nào không thuộc SDK openai đã phân loại) KHÔNG đi qua exception này —
    nó propagate nguyên vẹn để không bị nhầm thành "model không khả dụng"."""


# Sentinel trả về khi nội dung rỗng/không phải JSON — cố tình THIẾU field bắt buộc để
# bước validate ở structured_generate_sync trượt và kích hoạt repair-once.
_MALFORMED_SENTINEL = {"__malformed__": True}


def _cached_tokens(usage: Any) -> int | None:
    """Số token prompt được phục vụ từ prompt cache của provider.

    OpenAI phơi qua `usage.prompt_tokens_details.cached_tokens` (tự động, không cần
    cache_control). Trả None nếu provider/endpoint không báo cáo — không đoán bừa."""
    if usage is None:
        return None
    details = getattr(usage, "prompt_tokens_details", None)
    val = getattr(details, "cached_tokens", None) if details is not None else None
    return int(val) if isinstance(val, int) else None


def _usage_detail(usage: Any, group: str, field: str) -> int | None:
    """Read an optional nested usage counter without assuming provider support."""
    details = getattr(usage, group, None) if usage is not None else None
    value = getattr(details, field, None) if details is not None else None
    return int(value) if isinstance(value, int) else None

# Lỗi TẠM THỜI của provider — retry có ý nghĩa (mạng chập chờn, quá tải, hết quota tạm
# thời). Tên lớp (không phải instance) — import cục bộ trong hàm phân loại.
_TRANSIENT_EXCEPTION_NAMES = frozenset({"APITimeoutError", "APIConnectionError", "RateLimitError", "InternalServerError"})
# Lỗi CẤU HÌNH/XÁC THỰC — retry KHÔNG sửa được (sai key, sai quyền, request sai định
# dạng, model không tồn tại). Fail ngay, không tốn thêm round-trip.
_NON_RETRYABLE_EXCEPTION_NAMES = frozenset(
    {"AuthenticationError", "PermissionDeniedError", "BadRequestError", "NotFoundError", "UnprocessableEntityError", "ConflictError"}
)

# Provider TỪ CHỐI schema ở chế độ strict (hoặc endpoint không có route parse) — không phải
# lỗi tạm thời, retry vô nghĩa; đường đúng là rơi xuống fallback json_schema/json_object.
_STRICT_SCHEMA_REJECTED_EXCEPTION_NAMES = frozenset({"BadRequestError", "UnprocessableEntityError", "NotFoundError"})

# Schema mà endpoint hiện tại KHÔNG nhận ở chế độ strict — nhớ theo (model, tên schema) ở
# phạm vi MODULE, không phải instance: `build_nlu_model_client()` dựng client MỚI mỗi request
# nên cache theo instance sẽ quên sạch và trả giá 400 lại từ đầu ở từng lượt chat.
#
# Vì sao cần: các schema có field free-form (`CandidatePlan.params`, `SemanticGoal.target_state`
# kiểu dict[str, Any]) sinh ra object không thể đặt `additionalProperties: false`, mà strict mode
# của OpenAI bắt buộc điều đó → 400 ở MỌI lời gọi. Thử một lần rồi nhớ, thay vì lặp lại mãi.
_STRICT_SCHEMA_UNSUPPORTED: set[tuple[str, str]] = set()

# Optional request parameters rejected by a provider/model are discovered from the
# provider's structured 400 response and omitted on subsequent calls. The cache key
# includes endpoint + provider + model, so this is capability negotiation rather than
# a model-name allowlist. At present temperature is the only optional model parameter
# this client sends.
_OPTIONAL_MODEL_PARAMETERS = frozenset({"temperature", "prompt_cache_key"})
_UNSUPPORTED_OPTIONAL_PARAMETERS: set[tuple[str, str, str, str]] = set()

# Họ model SUY LUẬN (reasoning) — OpenAI gpt-5.* và o-series (o1/o3/o4). Chúng KHÔNG chấp
# nhận `temperature` (chỉ default=1) và tiêu token/độ trễ cho reasoning nội bộ; gọi kèm
# `temperature` → 400, còn để reasoning_effort mặc định (medium) → prompt eval dài dễ vượt
# timeout → APITimeoutError. Ta phát hiện theo TÊN model để gửi tham số ĐÚNG (bỏ temperature,
# thêm reasoning_effort) thay vì để mỗi lời gọi lãng phí một vòng 400-rồi-thử-lại.
_REASONING_MODEL_PREFIXES = ("gpt-5", "o1", "o3", "o4")
_VALID_REASONING_EFFORTS = ("minimal", "low", "medium", "high")


def is_reasoning_model(model: str) -> bool:
    """Model thuộc họ reasoning (không nhận temperature, nên set reasoning_effort)?"""
    name = (model or "").strip().lower()
    return any(name == p or name.startswith(p + "-") for p in _REASONING_MODEL_PREFIXES)


def _short_errors(exc: Exception, *, limit: int = 6) -> str:
    """Tóm tắt lỗi Pydantic thành 'field: message' để đưa vào repair prompt.

    Chỉ lấy vị trí field + thông điệp — KHÔNG in giá trị đầu vào (tránh vòng lặp bịa
    dữ liệu và tránh lộ nội dung nhạy cảm)."""
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return str(exc)[:300]
    lines = []
    for e in errors()[:limit]:
        loc = ".".join(str(p) for p in e.get("loc", ())) or "(root)"
        lines.append(f"- {loc}: {e.get('msg', 'invalid')}")
    return "\n".join(lines)


class OpenAICompatibleModelClient:
    """ModelClient chạy trên endpoint OpenAI-compatible."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None = None,
        timeout: float = 8.0,
        temperature: float = 0.0,
        provider: str = "openai",
        max_transport_retries: int = 2,
        retry_backoff_seconds: float = 0.3,
        reasoning_effort: str = "minimal",
        prompt_cache_enabled: bool = True,
        prompt_cache_namespace: str = "smarthome-semantic-v1",
    ) -> None:
        from openai import OpenAI  # import cục bộ: không có key thì không cần openai SDK

        from src.config import get_settings
        from src.observability.langsmith import wrap_openai_client

        # max_retries=0 ở SDK: TA tự kiểm soát retry (phân loại transient/non-retryable),
        # không để SDK âm thầm retry mọi loại lỗi theo policy riêng của nó.
        self._client = wrap_openai_client(
            OpenAI(api_key=api_key, base_url=base_url or None, timeout=timeout, max_retries=0),
            get_settings(),
        )
        self.model = model
        self.temperature = temperature
        self.request_timeout = timeout
        self.provider = provider
        self._is_reasoning = is_reasoning_model(model)
        effort = (reasoning_effort or "minimal").strip().lower()
        self.reasoning_effort = effort if effort in _VALID_REASONING_EFFORTS else "minimal"
        self._endpoint_identity = base_url or "openai-default"
        self._max_transport_retries = max(0, max_transport_retries)
        self._retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self.prompt_cache_enabled = bool(prompt_cache_enabled)
        self.prompt_cache_namespace = (prompt_cache_namespace or "smarthome-semantic-v1").strip()
        # Lịch sử lời gọi trong phiên hiện tại — cho eval/observability đọc.
        self.calls: list[ModelResponse] = []
        # Đo lường schema-repair TÁCH BIỆT với số lượt gọi node. `structured_calls` là số
        # lần một node yêu cầu output có cấu trúc (số lượt LOGIC); `schema_repairs` là số
        # lần lượt đầu sai contract và phải sửa. first_pass_validity = 1 - repairs/calls.
        # KHÔNG suy ra repair từ len(calls) — mỗi sample gọi nhiều node, len(calls) trộn cả
        # lượt node hợp lệ lẫn lượt sửa, không phản ánh tỉ lệ sai contract lần đầu.
        self.structured_calls = 0
        self.schema_repairs = 0

    def _tuning_params(self) -> dict[str, Any]:
        """Tham số điều chỉnh ĐÚNG theo họ model.

        Reasoning model (gpt-5.*/o-series): KHÔNG gửi `temperature` (chỉ nhận default) và
        gửi `reasoning_effort` (mặc định 'minimal') để giữ độ trễ thấp + không đói token cho
        completion → tránh APITimeoutError trên prompt eval dài. Model thường: gửi temperature.
        """
        if self._is_reasoning:
            return {"reasoning_effort": self.reasoning_effort}
        return {"temperature": self.temperature}

    def _prompt_cache_params(self, system_prompt: str, output_schema: type[BaseModel] | None) -> dict[str, Any]:
        """Stable, privacy-preserving routing key for requests sharing a prompt prefix."""
        if not self.prompt_cache_enabled or self.provider.strip().lower() != "openai":
            return {}
        schema_name = output_schema.__name__ if output_schema is not None else "json"
        digest = hashlib.sha256(
            f"{self.prompt_cache_namespace}\0{self.model}\0{schema_name}\0{system_prompt}".encode()
        ).hexdigest()[:32]
        return {"prompt_cache_key": f"smarthome-{digest}"}

    def _call_with_supported_parameters(self, call: Any, kwargs: dict[str, Any]) -> Any:
        """Retry once without an optional parameter the endpoint explicitly rejects."""
        request = dict(kwargs)
        prefix = (self._endpoint_identity, self.provider, self.model)
        for parameter in _OPTIONAL_MODEL_PARAMETERS:
            if (*prefix, parameter) in _UNSUPPORTED_OPTIONAL_PARAMETERS:
                request.pop(parameter, None)

        try:
            return call(**request)
        except Exception as exc:
            body = getattr(exc, "body", None)
            rejected_parameter = body.get("param") if isinstance(body, dict) else None
            code = body.get("code") if isinstance(body, dict) else None
            if (
                code in {"unsupported_value", "unsupported_parameter", "unknown_parameter"}
                and rejected_parameter in _OPTIONAL_MODEL_PARAMETERS
                and rejected_parameter in request
            ):
                _UNSUPPORTED_OPTIONAL_PARAMETERS.add((*prefix, rejected_parameter))
                logger.info(
                    "Endpoint không hỗ trợ tham số optional %s cho provider=%s model=%s; gọi lại không có tham số này",
                    rejected_parameter,
                    self.provider,
                    self.model,
                )
                request.pop(rejected_parameter)
                return call(**request)
            raise

    def reset(self) -> None:
        self.calls = []
        self.structured_calls = 0
        self.schema_repairs = 0

    @contextmanager
    def use_reasoning_effort(self, effort: str):  # noqa: ANN201
        """Temporarily raise/lower effort for one reasoning task on this client.

        A client is request-scoped in production.  Restoring the old value keeps
        the override safe for later lightweight nodes and for shared test clients.
        Non-reasoning models simply ignore this setting in ``_tuning_params``.
        """
        requested = (effort or "").strip().lower()
        previous = self.reasoning_effort
        if requested in _VALID_REASONING_EFFORTS:
            self.reasoning_effort = requested
        try:
            yield self
        finally:
            self.reasoning_effort = previous

    @contextmanager
    def use_planning_profile(self, model: str, effort: str, timeout: float):  # noqa: ANN201
        """Temporarily apply the larger budget needed by activity planning."""
        previous_model = self.model
        previous_is_reasoning = self._is_reasoning
        previous_effort = self.reasoning_effort
        previous_timeout = self.request_timeout
        if (model or "").strip():
            self.model = model.strip()
            self._is_reasoning = is_reasoning_model(self.model)
        requested = (effort or "").strip().lower()
        if requested in _VALID_REASONING_EFFORTS:
            self.reasoning_effort = requested
        if timeout > 0:
            self.request_timeout = timeout
        try:
            yield self
        finally:
            self.model = previous_model
            self._is_reasoning = previous_is_reasoning
            self.reasoning_effort = previous_effort
            self.request_timeout = previous_timeout

    def structured_generate_sync(
        self,
        prompt: str,
        output_schema: type[BaseModel],
        *,
        system_prompt: str | None = None,
        context: dict[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> Any:
        """Sinh output có cấu trúc CHO ĐÚNG nhiệm vụ của node gọi.

        Luồng: gọi (có transport-retry cho lỗi tạm thời) → parse+validate. Nếu JSON hỏng
        hoặc sai hình thức schema (KHÔNG phải lỗi transport) → sửa ĐÚNG MỘT lần kèm lỗi cụ
        thể, giữ nguyên ý hiểu, không bịa dữ liệu. Hỏng lần hai → ModelClientError (abstain).
        Lỗi transport hết retry ở BẤT KỲ lượt nào (kể cả lượt sửa) propagate ngay — không
        tính là một lần "sửa schema" thứ hai."""
        from src.nlu.prompts import INJECTION_DEFENSE, render_context_prompt

        ctx = {**(context or {})}
        ctx.setdefault("utterance", prompt)
        sys_prompt = f"{system_prompt}\n\n{INJECTION_DEFENSE}" if system_prompt else INJECTION_DEFENSE
        user_prompt = render_context_prompt(ctx, output_schema=output_schema)

        self.structured_calls += 1
        instance, err = self._attempt(sys_prompt, user_prompt, output_schema)
        if err is None:
            return instance

        # Lượt đầu sai contract → tính là một schema-repair (safety net, không phải happy path).
        self.schema_repairs += 1
        logger.warning("LLM output không hợp lệ (%s) — sửa đúng một lần", err)
        repair_prompt = (
            f"{user_prompt}\n\nLỖI CẦN SỬA (CHỈ sửa định dạng cho khớp schema {output_schema.__name__}, "
            f"GIỮ NGUYÊN ý hiểu, KHÔNG bịa dữ liệu):\n{err}"
        )
        instance, err2 = self._attempt(sys_prompt, repair_prompt, output_schema)
        if err2 is not None:
            logger.warning("LLM output vẫn không hợp lệ sau khi sửa — abstain: %s", err2)
            if self.calls and self.calls[-1].provider_error_type is None:
                self.calls[-1] = self.calls[-1].model_copy(update={"provider_error_type": "SchemaRepairFailed"})
            raise ModelClientError(f"SchemaRepairFailed: {err2}")
        return instance

    async def structured_generate(
        self,
        prompt: str,
        output_schema: type[BaseModel],
        *,
        system_prompt: str | None = None,
        context: dict[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> Any:
        return self.structured_generate_sync(
            prompt, output_schema, system_prompt=system_prompt, context=context, temperature=temperature
        )

    @property
    def total_tokens(self) -> int:
        return sum(c.total_tokens or 0 for c in self.calls)

    @property
    def total_cached_tokens(self) -> int:
        """Tổng token prompt phục vụ từ cache trong phiên (0 nếu provider không báo cáo)."""
        return sum(c.cached_tokens or 0 for c in self.calls)

    @property
    def total_input_tokens(self) -> int:
        return sum(c.input_tokens or 0 for c in self.calls)

    @property
    def cache_hit_ratio(self) -> float:
        """cached / input — tỉ lệ token đầu vào được cache. 0.0 nếu chưa có input."""
        inp = self.total_input_tokens
        return (self.total_cached_tokens / inp) if inp else 0.0

    def understand(self, *, utterance: str, context: dict[str, Any]) -> dict[str, Any]:
        """Legacy: trả JSON-dict theo một system prompt NLU chung (có transport-retry,
        KHÔNG schema-repair — caller tự validate). Pipeline mới dùng `structured_generate`;
        giữ hàm này cho tương thích ngược."""
        from src.nlu.prompts import INJECTION_DEFENSE, render_context_prompt

        sys = "Bạn là bộ hiểu ngôn ngữ của trợ lý nhà thông minh tiếng Việt. " + INJECTION_DEFENSE
        user_prompt = render_context_prompt({**context, "utterance": utterance})
        result = self._chat_with_transport_retry(sys, user_prompt)
        return result.model_dump() if isinstance(result, BaseModel) else result

    # ------------------------------------------------------------------
    # Tầng schema: gọi (có transport-retry) rồi parse+validate. KHÔNG raise
    # cho lỗi hình thức — trả (None, mô_tả_lỗi) để caller quyết định có sửa hay không.
    # Lỗi TRANSPORT hết retry vẫn raise ModelClientError ngay (không phục hồi bằng repair).
    # ------------------------------------------------------------------
    def _attempt(self, system_prompt: str, user_prompt: str, output_schema: type[BaseModel]) -> tuple[Any | None, str | None]:
        from pydantic import ValidationError

        res = self._chat_with_transport_retry(system_prompt, user_prompt, output_schema=output_schema)
        if isinstance(res, output_schema):
            return res, None
        if res == _MALFORMED_SENTINEL:
            return None, "malformed_or_empty_content"
        try:
            return output_schema.model_validate(res), None
        except ValidationError as exc:
            return None, _short_errors(exc)

    # ------------------------------------------------------------------
    # Tầng transport: MỘT round-trip HTTP mỗi attempt, retry CHỈ với lỗi tạm thời.
    # ------------------------------------------------------------------
    def _chat_with_transport_retry(
        self, system_prompt: str, user_prompt: str, output_schema: type[BaseModel] | None = None
    ) -> dict[str, Any] | BaseModel:
        import openai

        attempts = self._max_transport_retries + 1
        last_err_type = "Unexpected"
        for attempt in range(1, attempts + 1):
            started = time.perf_counter()
            try:
                return self._chat_once(system_prompt, user_prompt, output_schema=output_schema)
            except openai.APIError as exc:
                err_type = type(exc).__name__
                last_err_type = err_type
                latency = (time.perf_counter() - started) * 1000
                self.calls.append(
                    ModelResponse(
                        content=None,
                        model=self.model,
                        latency_ms=latency,
                        prompt_bytes=len(system_prompt.encode("utf-8")) + len(user_prompt.encode("utf-8")),
                        provider_error_type=err_type,
                    )
                )
                if err_type in _NON_RETRYABLE_EXCEPTION_NAMES:
                    logger.warning("Model provider lỗi cấu hình/xác thực (không retry): %s — abstain", err_type)
                    raise ModelClientError(err_type) from exc
                if err_type not in _TRANSIENT_EXCEPTION_NAMES:
                    # Loại lỗi API chưa phân loại — an toàn mặc định là KHÔNG retry mù quáng.
                    logger.warning("Model provider lỗi không phân loại (không retry): %s — abstain", err_type)
                    raise ModelClientError(err_type) from exc
                if attempt >= attempts:
                    logger.warning("Model provider lỗi tạm thời sau %d lần thử: %s — abstain", attempt, err_type)
                    raise ModelClientError(err_type) from exc
                logger.warning("Model provider lỗi tạm thời (%s) — thử lại lần %d/%d", err_type, attempt + 1, attempts)
                if self._retry_backoff_seconds > 0:
                    time.sleep(self._retry_backoff_seconds * attempt)
                continue
        # Không thể tới đây (vòng lặp luôn return hoặc raise), giữ lại cho type-checker.
        raise ModelClientError(last_err_type)

    def _chat_once(
        self, system_prompt: str, user_prompt: str, output_schema: type[BaseModel] | None = None
    ) -> dict[str, Any] | BaseModel:
        """Một lần gọi API, KHÔNG retry, KHÔNG phân loại lỗi — đó là việc của caller.
        Dùng native Structured Outputs với Pydantic schema khi có thể."""
        started = time.perf_counter()

        schema_key = (self.model, output_schema.__name__) if output_schema is not None else None
        if (
            output_schema is not None
            and schema_key not in _STRICT_SCHEMA_UNSUPPORTED
            and hasattr(self._client, "beta")
            and hasattr(self._client.beta, "chat")
        ):
            try:
                resp = self._call_with_supported_parameters(
                    self._client.beta.chat.completions.parse,
                    {
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        **self._tuning_params(),
                        **self._prompt_cache_params(system_prompt, output_schema),
                        "response_format": output_schema,
                        "timeout": self.request_timeout,
                    },
                )
                latency = (time.perf_counter() - started) * 1000
                choice = resp.choices[0] if resp.choices else None
                message = choice.message if choice else None
                parsed = getattr(message, "parsed", None) if message else None
                refusal = getattr(message, "refusal", None) if message else None
                usage = getattr(resp, "usage", None)

                self.calls.append(
                    ModelResponse(
                        content=getattr(message, "content", None) or (str(parsed) if parsed else None),
                        model=getattr(resp, "model", self.model),
                        input_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
                        output_tokens=getattr(usage, "completion_tokens", None) if usage else None,
                        total_tokens=getattr(usage, "total_tokens", None) if usage else None,
                        cached_tokens=_cached_tokens(usage),
                        cache_write_tokens=_usage_detail(usage, "prompt_tokens_details", "cache_write_tokens"),
                        reasoning_tokens=_usage_detail(usage, "completion_tokens_details", "reasoning_tokens"),
                        prompt_bytes=len(system_prompt.encode("utf-8")) + len(user_prompt.encode("utf-8")),
                        latency_ms=latency,
                        request_id=getattr(resp, "id", None),
                    )
                )

                if parsed is not None and not refusal:
                    return parsed
            except Exception as exc:
                # Lỗi TẠM THỜI (timeout/429/5xx) KHÔNG được nuốt ở đây: nuốt xong lại gọi
                # tiếp đường fallback là nhân đôi thời gian chờ cho cùng một sự cố mạng.
                # Để nó propagate cho tầng transport-retry phân loại và retry đúng cách.
                if type(exc).__name__ not in _STRICT_SCHEMA_REJECTED_EXCEPTION_NAMES:
                    raise
                _STRICT_SCHEMA_UNSUPPORTED.add(schema_key)  # type: ignore[arg-type]
                logger.info(
                    "Endpoint từ chối strict schema %s (%s) — chuyển sang fallback và ghi nhớ, không thử lại",
                    output_schema.__name__,
                    type(exc).__name__,
                )

        resp_format: dict[str, Any] = {"type": "json_object"}
        if output_schema is not None:
            try:
                resp_format = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": output_schema.__name__,
                        "schema": output_schema.model_json_schema(),
                    },
                }
            except Exception:
                resp_format = {"type": "json_object"}

        try:
            resp = self._call_with_supported_parameters(
                self._client.chat.completions.create,
                {
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    **self._tuning_params(),
                    **self._prompt_cache_params(system_prompt, output_schema),
                    "response_format": resp_format,
                    "timeout": self.request_timeout,
                },
            )
        except Exception as exc:
            if resp_format.get("type") == "json_schema" and type(exc).__name__ in ("BadRequestError", "UnprocessableEntityError"):
                resp = self._call_with_supported_parameters(
                    self._client.chat.completions.create,
                    {
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        **self._tuning_params(),
                        **self._prompt_cache_params(system_prompt, output_schema),
                        "response_format": {"type": "json_object"},
                        "timeout": self.request_timeout,
                    },
                )
            else:
                raise

        latency = (time.perf_counter() - started) * 1000
        content = resp.choices[0].message.content if resp.choices else None
        usage = getattr(resp, "usage", None)
        self.calls.append(
            ModelResponse(
                content=content,
                model=getattr(resp, "model", self.model),
                input_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
                output_tokens=getattr(usage, "completion_tokens", None) if usage else None,
                total_tokens=getattr(usage, "total_tokens", None) if usage else None,
                cached_tokens=_cached_tokens(usage),
                cache_write_tokens=_usage_detail(usage, "prompt_tokens_details", "cache_write_tokens"),
                reasoning_tokens=_usage_detail(usage, "completion_tokens_details", "reasoning_tokens"),
                prompt_bytes=len(system_prompt.encode("utf-8")) + len(user_prompt.encode("utf-8")),
                latency_ms=latency,
                request_id=getattr(resp, "id", None),
            )
        )

        if not content or not content.strip():
            logger.warning("Model trả nội dung rỗng")
            return dict(_MALFORMED_SENTINEL)
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            logger.warning("Model trả không phải JSON")
            return dict(_MALFORMED_SENTINEL)
        if not isinstance(data, dict):
            return dict(_MALFORMED_SENTINEL)
        return data


def build_nlu_model_client() -> OpenAICompatibleModelClient | None:
    """Dựng ModelClient production từ settings. None nếu chưa cấu hình đủ (→ offline)."""
    from src.config import get_settings

    cfg = get_settings().nlu_llm_config()
    if cfg is None:
        return None
    return OpenAICompatibleModelClient(
        api_key=str(cfg["api_key"]),
        model=str(cfg["model"]),
        base_url=str(cfg["base_url"]) or None,
        timeout=float(cfg["timeout"]),
        temperature=float(cfg["temperature"]),
        provider=str(cfg["provider"]),
        reasoning_effort=str(cfg.get("reasoning_effort", "minimal")),
        max_transport_retries=int(cfg.get("max_transport_retries", 1)),
        prompt_cache_enabled=bool(cfg.get("prompt_cache_enabled", True)),
        prompt_cache_namespace=str(cfg.get("prompt_cache_namespace", "smarthome-semantic-v1")),
    )
