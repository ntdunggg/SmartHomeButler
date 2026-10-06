"""Failure Handler (spec §52) — phân loại lỗi + retry có chặn.

Retry policy phải config; KHÔNG retry vô hạn (spec §52). Lỗi DEVICE_OFFLINE là loại
không nên retry ngay (thiết bị chưa lên) — đánh dấu để report; lỗi transient thì cho
retry tới `max_retries`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_retries: int = 1
    # Loại lỗi được phép retry (transient). DEVICE_OFFLINE mặc định KHÔNG retry.
    retryable: frozenset[str] = frozenset({"TRANSIENT", "TIMEOUT"})


def classify_failure(reason: str) -> str:
    """Chuẩn hoá lý do lỗi thành nhãn phân loại (spec §52 classify failure)."""
    token = (reason or "").upper()
    if "OFFLINE" in token:
        return "DEVICE_OFFLINE"
    if "TIMEOUT" in token:
        return "TIMEOUT"
    if "RANGE" in token:
        return "INVALID_TARGET"
    return "TRANSIENT"


def should_retry(reason: str, attempt: int, policy: RetryPolicy) -> bool:
    """Còn được retry không? (spec §52: có chặn, không vô hạn)."""
    if attempt >= policy.max_retries:
        return False
    return classify_failure(reason) in policy.retryable
