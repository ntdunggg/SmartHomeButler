"""Hạn ngạch LangSmith cạn không được làm ồn log hay chạm vào lượt suy luận.

Chốt §2026-08-30: quota trace tháng hết → SDK log lỗi 429 cho MỖI run ở luồng nền, hàng chục
dòng traceback mỗi phiên chat. Lượt suy luận vẫn đúng, nhưng log trông như agent đã hỏng.
"""

from __future__ import annotations

import logging

import pytest

from src.observability import langsmith


@pytest.fixture(autouse=True)
def _reset_guard():
    """Guard là singleton tiến trình — gỡ sạch giữa các test."""
    previous = langsmith._QUOTA_GUARD
    langsmith._QUOTA_GUARD = None
    yield
    for name in langsmith._TRACING_LOGGERS:
        log = logging.getLogger(name)
        for f in list(log.filters):
            if isinstance(f, langsmith._TracingQuotaGuard):
                log.removeFilter(f)
    langsmith._QUOTA_GUARD = previous


def _emit(message: str) -> bool:
    """Cho một bản ghi đi qua bộ lọc; True = vẫn được log."""
    guard = langsmith.install_tracing_failure_guard()
    record = logging.LogRecord("langsmith.client", logging.ERROR, __file__, 1, message, None, None)
    return guard.filter(record)


def test_loi_ingest_het_quota_chi_bao_mot_lan(caplog) -> None:
    quota_error = (
        "Failed to multipart ingest runs: langsmith.utils.LangSmithRateLimitError: "
        "Rate limit exceeded ... tenant exceeded usage limits"
    )
    with caplog.at_level(logging.WARNING, logger="src.observability.langsmith"):
        assert _emit(quota_error) is False  # bản ghi gốc bị chặn
        assert _emit(quota_error) is False
        assert _emit(quota_error) is False

    warnings = [r for r in caplog.records if "hạn ngạch" in r.getMessage()]
    assert len(warnings) == 1, "chỉ được cảnh báo MỘT lần, phần sau là tiếng ồn lặp"


def test_log_khong_lien_quan_van_di_qua() -> None:
    """Bộ lọc chỉ nuốt lỗi hạn ngạch — không được nuốt log chẩn đoán khác."""
    assert _emit("run tree submitted") is True
    assert _emit("connection refused") is True


def test_guard_idempotent_khong_chong_filter() -> None:
    first = langsmith.install_tracing_failure_guard()
    second = langsmith.install_tracing_failure_guard()
    assert first is second
    attached = [
        f for f in logging.getLogger("langsmith").filters
        if isinstance(f, langsmith._TracingQuotaGuard)
    ]
    assert len(attached) == 1
