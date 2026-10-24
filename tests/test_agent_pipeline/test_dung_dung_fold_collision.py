"""Regression tất định: va chạm bỏ dấu "đừng"↔"đúng" (cả hai fold thành "dung").

Bối cảnh (LIVE §2026-08-10, n02): "Đừng có bật tivi lên" bị nhận là CONFIRMATION (folded
"dung" khớp "đúng" trong _CONFIRM) rồi bị đẩy thành huỷ. Đối xứng: "đúng rồi" bị _NEGATION
(folded "dung" từ "đừng") gắn phủ định giả. Cả hai phải phân biệt đúng.
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.nlu.context import build_runtime_context
from src.nlu.normalizer import analyze
from src.nlu.ontology import UtteranceType
from src.nlu.understanding import understand

_NOW = datetime(2026, 8, 5, 22, 0, tzinfo=UTC)


def _u(utt: str):
    nu = analyze(utt, focus_room="Phòng khách")
    ctx = build_runtime_context(nu, now=_NOW, timezone="Asia/Ho_Chi_Minh", focus_room="Phòng khách")
    return nu, understand(nu, ctx)


def test_dung_negation_not_confirmation() -> None:
    """'đừng ...' là PHỦ ĐỊNH, không bao giờ là xác nhận."""
    for utt in ["Đừng có bật tivi lên", "đừng bật điều hoà", "đừng mở rèm phòng khách"]:
        nu, u = _u(utt)
        assert nu.has_negation is True, utt
        assert u.utterance_type != UtteranceType.CONFIRMATION, utt


def test_dung_confirmation_not_negation() -> None:
    """'đúng rồi/vậy/thế' là XÁC NHẬN, không bị gắn phủ định giả."""
    for utt in ["đúng rồi", "đúng vậy", "đúng thế"]:
        nu, u = _u(utt)
        assert nu.has_negation is False, utt
        assert u.utterance_type == UtteranceType.CONFIRMATION, utt


def test_real_confirmations_intact() -> None:
    for utt in ["ừ được", "đồng ý"]:
        _, u = _u(utt)
        assert u.utterance_type == UtteranceType.CONFIRMATION, utt
