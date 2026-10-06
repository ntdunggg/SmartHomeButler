"""Các lỗi grounding lộ ra từ bộ RAGAS 200 case tool-level (2026-08-30).

Bốn họ lỗi độc lập, cùng làm ToolCallAccuracy tụt xuống 0.798:

1. LIỆT KÊ bị hiểu thành MƠ HỒ — "bật đèn bếp và đèn bàn ăn" hỏi lại thay vì làm cả hai.
2. Bộ lọc phòng VỨT thiết bị được gọi đích danh — "mở rèm phòng khách và rèm bếp" mất vế sau.
3. Không PHÂN PHỐI loại thiết bị qua nhiều phòng — alias registry đều gắn phòng nên
   "mở cửa sổ phòng khách và phòng bố mẹ" chỉ khớp được vế đầu.
4. Va chạm ÂM TIẾT của từ ghép — "tóm tắt" chứa âm tiết "tắt" nên lật lệnh BẬT thành TẮT.

Kèm hai lỗi lượng từ/registry: "một nửa" không được hiểu, và thiết bị nhà KHÔNG CÓ thì bị hỏi
"phòng nào?" thay vì được báo là không có.
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.agent.cognitive.ledger import LedgerStore
from src.agent.memory.event_store import EventStore
from src.agent.memory.profile_store import ProfileStore
from src.agent.memory.turn_store import TurnStore
from src.agent.pipeline import PipelineDeps
from src.agent.preference.preference_store import PreferenceStore
from src.core.reasoning import FakeReasoningModel
from src.domain.enums import ActionType, Capability
from src.iot.registry import DEVICE_SPECS
from src.nlu.context import build_runtime_context
from src.nlu.normalizer import analyze
from src.nlu.understanding import unavailable_target, understand
from src.services.pipeline_bridge import reason

_NOW = datetime(2026, 8, 28, 20, 0, tzinfo=UTC)


def _goal(text: str):
    nu = analyze(text)
    return understand(nu, build_runtime_context(nu, now=_NOW)).goal


def _plan(text: str):
    model = FakeReasoningModel()
    deps = PipelineDeps(
        ledger_store=LedgerStore(),
        event_store=EventStore(),
        turn_store=TurnStore(),
        preference_store=PreferenceStore(),
        profile_store=ProfileStore(),
        model_client=model,
    )
    return reason(
        message=text,
        conversation_id=f"t:{text[:16]}",
        user_id="test",
        role="owner",
        now=_NOW,
        deps=deps,
        model_client=model,
        live_device_states={spec.slug: dict(spec.initial_state) for spec in DEVICE_SPECS},
    )


def _devices(result) -> list[str]:
    plan = result.candidate_plan
    return [a.device_id for a in (plan.actions if plan else [])]


# --- 1. Liệt kê ≠ mơ hồ ---------------------------------------------------------------------


def test_two_named_devices_are_both_acted_on():
    result = _plan("Bật đèn bếp và đèn bàn ăn.")
    assert result.outcome == "candidate_plan"
    assert _devices(result) == ["den_bep", "den_ban_an"]


def test_one_alias_matching_two_devices_still_asks():
    """"đèn ngủ" khớp HAI đèn ngủ khác phòng — đó mới là mơ hồ thật, phải hỏi lại."""
    assert _plan("Bật đèn ngủ.").outcome == "clarification"


# --- 2. Thiết bị gọi đích danh không bị bộ lọc phòng vứt -------------------------------------


def test_a_separately_named_device_survives_another_clause_room():
    result = _plan("Mở rèm phòng khách và rèm bếp.")
    assert _devices(result) == ["rem_phong_khach", "rem_bep"]


def test_room_still_vetoes_an_alias_from_a_room_never_mentioned():
    """Giữ nguyên phủ quyết an toàn: alias 'đèn trần' chỉ có ở Phòng khách, câu nói Phòng bếp."""
    assert "den_chum_phong_khach" not in _devices(_plan("Bật đèn trần phòng bếp."))


# --- 3. Phân phối loại thiết bị qua mọi phòng đã nêu -----------------------------------------


def test_a_device_type_distributes_over_every_named_room():
    goal = _goal("Mở cửa sổ phòng khách và phòng bố mẹ.")
    assert goal is not None
    assert goal.target_device_ids == ["cua_so_phong_khach", "cua_so_phong_bo_me"]


def test_distribution_also_completes_a_partially_matched_alias():
    goal = _goal("Tắt máy lọc phòng bố mẹ và phòng con.")
    assert goal is not None
    assert goal.target_device_ids == ["may_loc_phong_bo_me", "may_loc_phong_con"]


def test_a_single_room_command_is_unchanged():
    goal = _goal("Bật đèn chùm phòng khách.")
    assert goal is not None
    assert goal.target_device_ids == ["den_chum_phong_khach"]


# --- 4. Va chạm âm tiết trong từ ghép --------------------------------------------------------


def test_a_compound_word_does_not_flip_the_command():
    """"tóm tắt" chứa âm tiết "tắt"; trước bản vá câu này TẮT TV thay vì bật."""
    goal = _goal("Bật TV phòng bố mẹ rồi tóm tắt một cuốn sách.")
    assert goal is not None
    assert goal.action_hint == "turn_on"


def test_the_real_off_verb_still_works():
    for text, expected in (
        ("Tắt TV phòng bố mẹ.", "turn_off"),
        ("Tắt tất cả đèn.", "turn_off"),
        ("Đóng rèm phòng khách.", "turn_off"),
        ("Khởi động robot hút bụi.", "turn_on"),
    ):
        goal = _goal(text)
        assert goal is not None and goal.action_hint == expected, text


# --- 5. Lượng từ phân số ---------------------------------------------------------------------


def test_half_is_a_value_on_the_first_turn_too():
    """Trước bản vá chỉ lượt TIẾP NỐI hiểu "một nửa"; lượt đầu mở rèm hết 100%."""
    result = _plan("Mở rèm phòng khách khoảng một nửa.")
    action = result.candidate_plan.actions[0]
    assert action.capability == Capability.POSITION
    assert action.action == ActionType.SET
    assert action.params.get("percent") == 50


def test_more_is_not_half():
    """"nữa" (thêm) chỉ khác "nửa" (½) ở dấu — gộp lại sẽ biến lệnh tăng thành đặt 50%."""
    goal = _goal("Tăng độ sáng đèn chùm phòng khách nữa.")
    assert goal is not None
    assert goal.parameters.get("percent") != 50


# --- 6. Thiết bị nhà không có ----------------------------------------------------------------


def test_a_device_type_the_home_lacks_is_reported_not_asked_about():
    result = _plan("Bật quạt trần phòng khách.")
    assert result.outcome == "answer"
    assert "không có" in result.reply
    assert "phòng nào" not in result.reply


def test_unavailable_target_names_the_users_own_word():
    assert unavailable_target(analyze("Bật quạt trần phòng khách.")) == "quạt"
    assert unavailable_target(analyze("Bật máy sưởi phòng con.")) == "máy sưởi"


def test_a_device_the_home_has_is_never_flagged_unavailable():
    for text in ("Bật đèn chùm phòng khách.", "Mở rèm phòng bếp.", "Khoá cửa ra vào chính."):
        assert unavailable_target(analyze(text)) is None, text


def test_a_bare_room_complaint_is_not_an_unavailable_target():
    """"phòng" cũng là danh từ trần làm chủ ngữ — không được biến thành "nhà không có phòng bí"."""
    assert unavailable_target(analyze("Phòng bí quá khó thở.")) is None


# --- 7. Đặt mức hàm ý bật thiết bị -----------------------------------------------------------


def test_setting_a_level_implies_powering_the_device_on():
    """Đặt âm lượng cho chiếc loa ĐANG TẮT là thao tác người dùng không nghe thấy gì."""
    from src.domain.action_registry import semantic_expected_state

    expected = semantic_expected_state(Capability.VOLUME, ActionType.SET, {"percent": 15})
    assert expected.get("power") == "on"
    assert expected.get("volume") == 15


# --- 8. Lượng từ nhóm đếm được ---------------------------------------------------------------


def test_a_counted_group_quantifier_targets_every_device():
    """"cả ba cửa sổ" là lượng từ nhóm; trước bản vá câu này không ra hành động nào."""
    assert _devices(_plan("Đóng cả ba cửa sổ trong nhà.")) == [
        "cua_so_phong_khach",
        "cua_so_phong_bo_me",
        "cua_so_phong_con",
    ]


def test_bare_ca_is_not_a_group_quantifier():
    """"cả" trần chỉ là từ nhấn — không được gom cả nhà khi câu đã nêu đúng hai phòng."""
    assert _devices(_plan("Hạ cả điều hòa phòng bố mẹ và phòng con xuống 24 độ.")) == [
        "dieu_hoa_phong_bo_me",
        "dieu_hoa_phong_con",
    ]


# --- 9. Mệnh đề ghép: giá trị và ý định nguồn đi theo đúng thiết bị của mệnh đề ---------------


def test_each_clause_keeps_its_own_value():
    """Trước bản vá `parameters` là của CẢ CÂU, nên vế `set` bị dựng thiếu params rồi rớt ở
    validator — người dùng mất hẳn một vế mà không được báo gì."""
    actions = _plan("Mở rèm phòng bếp trước, sau đó chỉnh đèn bàn ăn xuống 50%.").candidate_plan.actions
    assert [(a.device_id, a.action.value, a.params) for a in actions] == [
        ("rem_bep", "open", {}),
        ("den_ban_an", "set", {"percent": 50}),
    ]


def test_an_elliptical_clause_inherits_the_previous_device():
    """"...rồi đặt âm lượng xuống 15" không nhắc lại thiết bị vì nó nói về chính chiếc loa vừa bật."""
    actions = _plan("Bật loa bếp trước rồi đặt âm lượng xuống 15.").candidate_plan.actions
    assert [(a.device_id, a.capability.value, a.action.value) for a in actions] == [
        ("loa_bep", "on_off", "turn_on"),
        ("loa_bep", "volume", "set"),
    ]
    assert actions[1].params == {"percent": 15}


def test_turning_on_at_a_level_does_both():
    """"bật đèn ngủ Ở 30%" = nguồn + mức; một ProposalAction chỉ mang được một capability."""
    actions = _plan("Đóng rèm phòng bố mẹ rồi bật đèn ngủ bố mẹ ở 30%.").candidate_plan.actions
    assert [(a.device_id, a.capability.value) for a in actions] == [
        ("rem_phong_bo_me", "position"),
        ("den_ngu_bo_me", "on_off"),
        ("den_ngu_bo_me", "brightness"),
    ]
    assert actions[2].params == {"percent": 30}


def test_power_intent_survives_inside_a_single_clause():
    actions = _plan("Đóng cửa sổ phòng con, sau đó bật điều hòa phòng con ở 24 độ.").candidate_plan.actions
    assert [(a.device_id, a.capability.value) for a in actions] == [
        ("cua_so_phong_con", "position"),
        ("dieu_hoa_phong_con", "on_off"),
        ("dieu_hoa_phong_con", "temperature"),
    ]


def test_opposite_actions_on_one_device_remain_a_real_conflict():
    """"bật rồi tắt" cùng một thiết bị KHÔNG được gộp — chỉ các vế cùng cực tính mới bổ sung nhau."""
    devices = _devices(_plan("Bật đèn bếp rồi tắt đèn bếp."))
    assert devices == ["den_bep"]


def test_a_plain_set_command_does_not_gain_a_power_action():
    """Không có động từ nguồn trong câu thì không được tự thêm bước bật."""
    actions = _plan("Chỉnh đèn chùm phòng khách xuống 45% độ sáng.").candidate_plan.actions
    assert [(a.device_id, a.capability.value, a.params) for a in actions] == [
        ("den_chum_phong_khach", "brightness", {"percent": 45})
    ]


def test_a_bare_number_with_a_dimension_noun_keeps_its_value():
    """Khoá số trung tính `value` phải được nhận, nếu không SET rơi xuống TURN_ON và mất số."""
    actions = _plan("Chỉnh âm lượng TV phòng khách còn 25.").candidate_plan.actions
    assert [(a.device_id, a.action.value, a.params) for a in actions] == [
        ("tv_phong_khach", "set", {"percent": 25})
    ]
