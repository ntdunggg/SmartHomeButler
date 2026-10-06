"""QA findings 2+3 — generalization: N mệnh đề độc lập trong MỘT lượt, mỗi mệnh đề tự có
thiết bị + hành động + phạm vi phủ định RIÊNG (không dùng chung action_hint/has_negation toàn
câu). Bộ splitter tổng quát (`src.nlu.exclusion_clause.split_command_clauses` +
`src.nlu.understanding._resolve_independent_clauses`) phải nhận diện được ranh giới mệnh đề
qua BẤT KỲ tổ hợp dấu phẩy / "và" / "rồi" / "còn ... thì" / "nhưng" — không chỉ MỘT liên từ cố
định — và phạm vi phủ định phải bám đúng mệnh đề chứa nó, không lan sang thiết bị của mệnh đề
khác (an toàn: sai thì phải fail-safe về answer/clarification, KHÔNG BAO GIỜ được lọt một hành
động đúng-thiết-bị-vừa-bị-phủ-định vào candidate_plan).

Câu ở đây do Coder TỰ NGHĨ (round 2, sau khi Tech Lead phát hiện bộ test round 1 chỉ có 1 câu/
mục và không tổng quát) — khác cấu trúc với:
  - câu benchmark gốc của QA (test_qa_static_audit_findings.py, dùng "còn ... thì" / "đừng X,
    nhưng Y" đúng MỘT dạng mỗi mục),
  - 16 câu probe của Tech Lead (chỉ dùng để CHẨN ĐOÁN cơ chế lỗi lúc sửa, không phải bộ nghiệm
    thu — không copy nguyên văn vào đây).
QA sẽ tự soạn thêm một bộ giữ kín riêng để xác nhận độc lập."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.agent.cognitive.ledger import LedgerStore
from src.agent.memory.event_store import EventStore
from src.agent.memory.profile_store import ProfileStore
from src.agent.memory.turn_store import TurnStore
from src.agent.pipeline import PipelineDeps
from src.agent.preference.preference_store import PreferenceStore
from src.core.reasoning import FakeReasoningModel
from src.services.pipeline_bridge import reason

NOW = datetime(2026, 8, 24, 20, 0, tzinfo=UTC)


def _deps() -> PipelineDeps:
    return PipelineDeps(
        ledger_store=LedgerStore(), event_store=EventStore(), turn_store=TurnStore(),
        preference_store=PreferenceStore(), profile_store=ProfileStore(), model_client=FakeReasoningModel(),
    )


def _reason(msg: str, conv_id: str, **kw):
    return reason(
        message=msg, conversation_id=conv_id, now=NOW,
        model_client=FakeReasoningModel(), deps=_deps(), **kw,
    )


def _plan_by_device(r) -> dict[str, str]:
    if r.candidate_plan is None:
        return {}
    return {a.device_id: a.action for a in r.candidate_plan.actions}


# ---------------------------------------------------------------------------
# Mục 2 — câu ghép ≥2 hành động KHÁC NHAU cho ≥2 đích khác nhau: mỗi liên từ/dấu câu khác nhau,
# mỗi cặp hành động khác nhau (bật/tắt, khoá/mở khoá, đóng/mở, tăng/giảm âm lượng).
# ---------------------------------------------------------------------------

MUC2_CASES = [
    (
        "m2p-1", "Phòng khách", "Mở cửa sổ phòng khách, khoá cửa chính lại.",
        {"cua_so_phong_khach": "open", "khoa_cua_chinh": "lock"},
    ),
    (
        "m2p-2", "Phòng ngủ con", "Bật đèn ngủ phòng con và tắt điều hoà phòng con đi.",
        {"den_ngu_con": "turn_on", "dieu_hoa_phong_con": "turn_off"},
    ),
    (
        "m2p-3", "Phòng khách", "Tắt loa phòng khách rồi bật tivi phòng khách lên.",
        {"loa_phong_khach": "turn_off", "tv_phong_khach": "turn_on"},
    ),
    (
        "m2p-4", "Phòng khách", "Giảm âm lượng tivi phòng khách xuống, còn loa bếp thì tăng lên.",
        {"tv_phong_khach": "decrease", "loa_bep": "increase"},
    ),
    (
        "m2p-5", "Phòng ngủ bố mẹ", "Mở khoá cửa phòng bố mẹ ra, khoá cửa chính lại.",
        {"khoa_cua_phong_bo_me": "unlock", "khoa_cua_chinh": "lock"},
    ),
    (
        "m2p-6", "Phòng khách", "Đóng rèm phòng bố mẹ lại, mở cửa sổ phòng khách ra.",
        {"rem_phong_bo_me": "close", "cua_so_phong_khach": "open"},
    ),
    (
        "m2p-7", "Phòng ngủ bố mẹ", "Bật đèn bàn học lên nhưng tắt đèn ngủ bố mẹ đi.",
        {"den_ban_hoc": "turn_on", "den_ngu_bo_me": "turn_off"},
    ),
    (
        "m2p-8", "Phòng khách", "Tắt máy lọc phòng con đi, bật máy lọc phòng khách lên.",
        {"may_loc_phong_con": "turn_off", "may_loc_phong_khach": "turn_on"},
    ),
]


@pytest.mark.parametrize("case_id,loc,text,expected", MUC2_CASES, ids=[c[0] for c in MUC2_CASES])
def test_compound_two_actions_paraphrases(case_id, loc, text, expected):
    r = _reason(text, case_id, speaker_location=loc)
    assert r.candidate_plan is not None, f"[{case_id}] phải ra kế hoạch: {text!r} (reply={r.reply!r})"
    by_device = _plan_by_device(r)
    for device_id, action in expected.items():
        assert by_device.get(device_id) == action, (
            f"[{case_id}] {text!r}: kỳ vọng {device_id}={action}, thực tế plan={by_device}"
        )
    # Không được để hai đích khác nhau vô tình nhận CÙNG một hành động khi câu đòi 2 khác nhau.
    assert len(set(expected.values())) < 2 or len({by_device.get(d) for d in expected}) >= 2, (
        f"[{case_id}] {text!r}: hai thiết bị bị gộp về cùng một hành động: {by_device}"
    )


def test_muc2_has_at_least_eight_paraphrases():
    assert len(MUC2_CASES) >= 8


# ---------------------------------------------------------------------------
# Mục 3 — mệnh đề PHỦ ĐỊNH một thiết bị + mệnh đề DƯƠNG độc lập cho thiết bị KHÁC, với đủ loại
# từ phủ định (đừng / không cần / chưa ... vội) và liên từ (dấu phẩy / và / còn ... thì / nhưng),
# CẢ HAI thứ tự (phủ định trước, phủ định sau).
# ---------------------------------------------------------------------------

MUC3_CASES = [
    (
        "m3p-1", "Phòng ngủ con", "Đèn ngủ con thì không cần tắt, nhưng tắt máy lọc phòng con đi.",
        "den_ngu_con", {"may_loc_phong_con": "turn_off"},
    ),
    (
        "m3p-2", "Phòng khách",
        "Chưa mở cửa sổ phòng khách vội, còn rèm phòng khách thì đóng lại giúp mình.",
        "cua_so_phong_khach", {"rem_phong_khach": "close"},
    ),
    (
        "m3p-3", "Phòng khách", "Đừng tắt tivi phòng khách, tắt loa phòng khách đi.",
        "tv_phong_khach", {"loa_phong_khach": "turn_off"},
    ),
    (
        "m3p-4", "Phòng khách", "Đừng khoá cửa chính vội, và bật đèn bàn ăn lên giúp mình.",
        "khoa_cua_chinh", {"den_ban_an": "turn_on"},
    ),
    (
        "m3p-5", "Phòng ngủ bố mẹ", "Bật đèn bàn học lên, nhưng đừng bật đèn ngủ bố mẹ.",
        "den_ngu_bo_me", {"den_ban_hoc": "turn_on"},
    ),
    (
        "m3p-6", "Phòng ngủ con", "Không cần bật quạt máy lọc phòng con đâu, nhưng mở rèm phòng con ra.",
        "may_loc_phong_con", {"rem_phong_con": "open"},
    ),
    (
        "m3p-7", "Phòng khách", "Camera cửa chính thì đừng khoá, còn cửa chính thì khoá lại giúp mình.",
        "camera_cua_chinh", {"khoa_cua_chinh": "lock"},
    ),
    (
        "m3p-8", "Phòng bếp", "Đừng đóng rèm bếp, bật đèn bếp lên giúp mình.",
        "rem_bep", {"den_bep": "turn_on"},
    ),
]


@pytest.mark.parametrize(
    "case_id,loc,text,negated_device,expected", MUC3_CASES, ids=[c[0] for c in MUC3_CASES]
)
def test_negation_scoped_to_own_clause_paraphrases(case_id, loc, text, negated_device, expected):
    r = _reason(text, case_id, speaker_location=loc)
    assert r.outcome != "answer" or r.candidate_plan is not None, (
        f"[{case_id}] {text!r}: mệnh đề dương bị nuốt mất (outcome=answer, reply={r.reply!r})"
    )
    assert r.candidate_plan is not None, (
        f"[{case_id}] {text!r}: phải ra được kế hoạch cho mệnh đề dương (reply={r.reply!r})"
    )
    by_device = _plan_by_device(r)
    assert negated_device not in by_device, (
        f"[{case_id}] {text!r}: thiết bị VỪA BỊ PHỦ ĐỊNH ({negated_device}) không được lọt vào "
        f"kế hoạch — an toàn: sai phạm vi phủ định tuyệt đối không được biến thành hành động "
        f"thật trên đúng thiết bị người dùng vừa cấm. plan={by_device}"
    )
    for device_id, action in expected.items():
        assert by_device.get(device_id) == action, (
            f"[{case_id}] {text!r}: kỳ vọng {device_id}={action}, thực tế plan={by_device}"
        )


def test_muc3_has_at_least_eight_paraphrases():
    assert len(MUC3_CASES) >= 8


# ---------------------------------------------------------------------------
# An toàn cứng: phủ định gán nhầm KHÔNG BAO GIỜ được lọt thành candidate_plan chứa đúng
# thiết bị/hành động vừa bị phủ định — kể cả khi cụm phủ định không nằm trong tập mẫu "đừng/
# không cần" đơn giản (Tech Lead phát hiện: "chưa X vội... đâu" từng lọt qua thành plan SAI).
# ---------------------------------------------------------------------------

def test_chua_voi_dau_negation_never_leaks_into_plan_for_negated_device():
    """"Chưa tắt vội máy lọc phòng bố mẹ đâu, nhưng tắt đèn bàn làm việc đi." — trước bản vá,
    plan generat ra CẢ HAI thành turn_off (kể cả máy lọc VỪA bị phủ định tường minh) vì "chưa X"
    không được `_NEGATION`/`_NEGATION_VERB` nhận diện. Đây là lỗi an toàn/đúng-đắn (không chỉ
    UX): nếu lọt tới dispatch thật, thiết bị sẽ bị tắt TRÁI YÊU CẦU tường minh của người dùng."""
    deps = _deps()
    r = reason(
        message="Chưa tắt vội máy lọc phòng bố mẹ đâu, nhưng tắt đèn bàn làm việc đi.",
        conversation_id="safety-chua-voi", now=NOW, model_client=FakeReasoningModel(), deps=deps,
        focus_room="Phòng ngủ bố mẹ",
    )
    assert r.candidate_plan is not None, f"phải ra kế hoạch cho mệnh đề dương (reply={r.reply!r})"
    by_device = _plan_by_device(r)
    assert "may_loc_phong_bo_me" not in by_device, (
        f"AN TOÀN: máy lọc phòng bố mẹ VỪA bị phủ định tường minh ('chưa tắt vội...đâu') không "
        f"được xuất hiện trong plan dù dưới bất kỳ hành động nào. plan={by_device}"
    )
    assert by_device.get("den_ban_lam_viec") == "turn_off"

    # Lớp phòng thủ thứ hai, ĐỘC LẬP với tầng understanding (spec §49/§73.6-7 trust boundary):
    # excluded_device_ids của goal phải được ghi thành ràng buộc "avoid:" trong Requirement
    # Ledger CÙNG LƯỢT — validate_plan (harness) sẽ chặn MỌI action trên thiết bị này nếu một
    # tầng khác (Manager/specialists/LLM open-ended) vẫn lỡ đề xuất nó, dù understanding có bug.
    assert r.semantic_goal is not None
    assert "may_loc_phong_bo_me" in r.semantic_goal.excluded_device_ids
    ledger = deps.ledger_store.load("safety-chua-voi")
    assert "avoid:may_loc_phong_bo_me" in ledger.constraints, (
        f"validate/policy layer safety net (ledger avoid-constraint) không được ghi: {ledger.constraints}"
    )


def test_pure_single_clause_negation_still_answers_without_plan():
    """Regression: câu chỉ có MỘT mệnh đề phủ định thuần tuý (không mệnh đề dương nào khác)
    vẫn phải trả lời "sẽ không làm", KHÔNG lập kế hoạch nào — bộ tách N-mệnh-đề không được tự
    tạo ra một mệnh đề dương giả khi câu thực chất chỉ có một ý phủ định."""
    r = _reason("Đừng bật đèn chùm phòng khách nhé.", "single-negation-regress")
    assert r.candidate_plan is None
    assert r.outcome == "answer"


def test_still_lai_phrase_is_not_treated_as_clause_boundary():
    """Regression cho chính bộ splitter mới: "còn lại" (remaining) là MỘT CỤM CỐ ĐỊNH, không
    phải liên từ "còn" nối hai mệnh đề — "tắt hết đèn còn lại trong bếp" không được tách nhầm
    thành "tắt hết đèn" + "lại trong bếp" (sẽ làm mất thiết bị)."""
    r = _reason(
        "Đèn bàn ăn thì đừng tắt, nhưng tắt hết đèn còn lại trong bếp đi.",
        "still-lai-regress", focus_room="Phòng bếp",
    )
    assert r.candidate_plan is not None
    by_device = _plan_by_device(r)
    assert "den_ban_an" not in by_device
    assert by_device.get("den_bep") == "turn_off"


# ---------------------------------------------------------------------------
# PO round-3 finding (q3-6): "kéo" (rèm/mành) thiếu trong danh sách đồng nghĩa curated của
# _TURN_ON/_TURN_OFF ở src/nlu/understanding.py — mệnh đề dương dùng "kéo" không được
# `_device_action_hint()` nhận diện hành động → split bị huỷ (an toàn) nhưng rơi về fallback cũ
# sai. Root-cause fix: thêm "kéo <đối tượng> lại/vào" (đóng) và "kéo <đối tượng> ra/lên" (mở)
# vào ĐÚNG 1 nguồn chân lý dùng chung (không thêm "kéo" trần mơ hồ 2 chiều). Vì là regex dùng
# chung cho `_device_action_hint`, sửa 1 nơi áp dụng cho CẢ mục 2 lẫn mục 3 — các case dưới đây
# cố tình trộn cả hai kiểu câu ghép để đo tổng quát thật, không chỉ patch riêng q3-6.
# ---------------------------------------------------------------------------

def test_po_q3_6_exact_sentence_kep_lai_closes_correct_curtain():
    """Câu chính xác PO nêu trong đợt reject — kiểm tra trực tiếp bằng chứng root-cause đã sửa."""
    r = _reason(
        "Đừng đóng cửa sổ phòng con, nhưng kéo rèm phòng con lại giúp mình.",
        "q3-6-exact", speaker_location="Phòng ngủ con",
    )
    assert r.candidate_plan is not None, f"phải ra kế hoạch cho mệnh đề dương (reply={r.reply!r})"
    by_device = _plan_by_device(r)
    assert "cua_so_phong_con" not in by_device, "cửa sổ VỪA bị phủ định không được lọt vào plan"
    assert by_device.get("rem_phong_con") == "close"


@pytest.mark.parametrize(
    "case_id,loc,text,expected",
    [
        (
            "keo-gen-1", "Phòng bếp", "Kéo rèm bếp ra giúp mình, còn đèn bếp thì tắt đi.",
            {"rem_bep": "open", "den_bep": "turn_off"},
        ),
        (
            "keo-gen-2", "Phòng ngủ bố mẹ", "Kéo rèm phòng bố mẹ vào, rồi bật đèn ngủ bố mẹ lên.",
            {"rem_phong_bo_me": "close", "den_ngu_bo_me": "turn_on"},
        ),
    ],
    ids=["keo-gen-1", "keo-gen-2"],
)
def test_keo_verb_generalizes_across_rooms_and_connectors(case_id, loc, text, expected):
    """"Kéo" phải hoạt động đúng chiều bất kể phòng/thiết bị/liên từ nối mệnh đề nào — không
    chỉ đúng MỘT câu q3-6 (mục 2: hai mệnh đề dương độc lập dùng "kéo" + động từ khác)."""
    r = _reason(text, case_id, speaker_location=loc)
    assert r.candidate_plan is not None, f"[{case_id}] phải ra kế hoạch (reply={r.reply!r})"
    by_device = _plan_by_device(r)
    for device_id, action in expected.items():
        assert by_device.get(device_id) == action, (
            f"[{case_id}] {text!r}: kỳ vọng {device_id}={action}, thực tế plan={by_device}"
        )


def test_keo_verb_scoped_correctly_when_the_negated_clause_uses_keo():
    """"Đừng kéo rèm phòng khách vào, nhưng bật đèn chùm phòng khách lên." — mệnh đề PHỦ ĐỊNH
    (không phải mệnh đề dương) dùng "kéo": has_negation vẫn nhận qua "đừng" trần (không phụ
    thuộc verb-vocabulary của "kéo"), rèm không được đụng tới, đèn chùm phải bật."""
    r = _reason(
        "Đừng kéo rèm phòng khách vào, nhưng bật đèn chùm phòng khách lên.",
        "keo-gen-3", speaker_location="Phòng khách",
    )
    assert r.candidate_plan is not None, f"phải ra kế hoạch (reply={r.reply!r})"
    by_device = _plan_by_device(r)
    assert "rem_phong_khach" not in by_device
    assert by_device.get("den_chum_phong_khach") == "turn_on"


def test_keo_verb_as_positive_clause_when_negation_targets_a_different_device():
    """"Đừng tắt đèn ngủ con, còn rèm phòng con thì kéo lại giúp mình." — chiều ngược của case
    trên: mệnh đề DƯƠNG dùng "kéo", mệnh đề phủ định nhắm một thiết bị khác."""
    r = _reason(
        "Đừng tắt đèn ngủ con, còn rèm phòng con thì kéo lại giúp mình.",
        "keo-gen-4", speaker_location="Phòng ngủ con",
    )
    assert r.candidate_plan is not None, f"phải ra kế hoạch (reply={r.reply!r})"
    by_device = _plan_by_device(r)
    assert "den_ngu_con" not in by_device
    assert by_device.get("rem_phong_con") == "close"


def test_keo_fix_does_not_affect_critical_negation_safety_case():
    """Re-verify bắt buộc theo yêu cầu QA: thay đổi regex "kéo" lần này KHÔNG được ảnh hưởng gì
    tới case an toàn nghiêm trọng đã sửa ở vòng trước ("chưa X vội...đâu")."""
    deps = _deps()
    r = reason(
        message="Chưa tắt vội máy lọc phòng bố mẹ đâu, nhưng tắt đèn bàn làm việc đi.",
        conversation_id="keo-fix-safety-recheck", now=NOW, model_client=FakeReasoningModel(), deps=deps,
        focus_room="Phòng ngủ bố mẹ",
    )
    assert r.candidate_plan is not None
    by_device = _plan_by_device(r)
    assert "may_loc_phong_bo_me" not in by_device
    assert by_device.get("den_ban_lam_viec") == "turn_off"
