"""Vòng đời ràng buộc người dùng (§18, bất biến #8) — tích luỹ VÀ thu hồi.

Bất biến #8 ("constraint sống qua nhiều lượt") bảo vệ lệnh cấm khỏi bị một lượt sau VÔ TÌNH
xoá. Trước bản vá, `ledger.constraints` không có đường thu hồi nào trong toàn bộ codebase, nên
nó bảo vệ luôn cả trường hợp CHÍNH người dùng đổi ý: "đừng bật đèn chùm" rồi "bật đèn chùm đi"
bị validator chặn vĩnh viễn bằng EXPLICIT_CONSTRAINT_VIOLATION, và bridge trả về một câu SAI
SỰ THẬT ("đã ở trạng thái phù hợp") giấu mất lý do.

Ranh giới cần giữ: chỉ LỆNH TRỰC TIẾP, tất định, gọi đích danh thiết bị mới được lật; mục tiêu
suy diễn (comfort) và routine phát lại thì không — nếu không agent có thể tự nói mình thoát
khỏi lệnh cấm của người dùng.
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.agent.cognitive.ledger import LedgerStore
from src.agent.cognitive.ledger_updater import revoke_superseded_constraints, update_ledger
from src.agent.pipeline import PipelineDeps
from src.agent.schemas import RequirementLedger, SemanticGoal
from src.core.reasoning import FakeReasoningModel
from src.nlu.ontology import UtteranceType
from src.services.pipeline_bridge import reason

_NOW = datetime(2026, 8, 28, 20, 0, tzinfo=UTC)


def _goal(**kw) -> SemanticGoal:
    base = dict(
        intent="x",
        raw_utterance="x",
        utterance_type=UtteranceType.DEVICE_COMMAND,
        confidence=0.9,
        target_devices_deterministic=True,
    )
    base.update(kw)
    return SemanticGoal(**base)


def _run(conversation_id: str, *turns: str, location: str | None = None):
    model = FakeReasoningModel()
    deps = PipelineDeps(model_client=model)
    results = [
        reason(
            message=text,
            conversation_id=conversation_id,
            user_id=f"test:{conversation_id}",
            role="owner",
            now=_NOW,
            speaker_location=location if index == 0 else None,
            deps=deps,
            model_client=model,
        )
        for index, text in enumerate(turns)
    ]
    return deps, results


def _devices(result) -> list[str]:
    plan = result.candidate_plan
    return [action.device_id for action in (plan.actions if plan else [])]


# --- Luật thu hồi ở mức đơn vị -------------------------------------------------------------


def test_direct_opposite_command_revokes_keep_off():
    ledger = RequirementLedger(constraints=["keep_off:den_chum_phong_khach"])
    revoked = revoke_superseded_constraints(
        ledger, _goal(action_hint="turn_on", target_device_ids=["den_chum_phong_khach"])
    )
    assert revoked == ["keep_off:den_chum_phong_khach"]
    assert ledger.constraints == []


def test_direct_opposite_command_revokes_keep_on():
    ledger = RequirementLedger(constraints=["keep_on:dieu_hoa_phong_khach"])
    revoke_superseded_constraints(
        ledger, _goal(action_hint="turn_off", target_device_ids=["dieu_hoa_phong_khach"])
    )
    assert ledger.constraints == []


def test_any_direct_command_revokes_avoid():
    """`avoid:X` nghĩa là "đừng dùng X" — mọi lệnh trực tiếp lên X đều là một lần đổi ý."""
    ledger = RequirementLedger(constraints=["avoid:dieu_hoa_phong_khach"])
    revoke_superseded_constraints(
        ledger, _goal(action_hint="turn_on", target_device_ids=["dieu_hoa_phong_khach"])
    )
    assert ledger.constraints == []


def test_revocation_releases_the_matching_rejected_assumption():
    """Nếu chỉ gỡ constraint, validator vẫn chặn cùng hành động qua nhánh `rejected:`."""
    ledger = RequirementLedger(
        constraints=["keep_off:den_chum_phong_khach"],
        rejected_assumptions=["turn_on:den_chum_phong_khach"],
    )
    revoke_superseded_constraints(
        ledger, _goal(action_hint="turn_on", target_device_ids=["den_chum_phong_khach"])
    )
    assert ledger.rejected_assumptions == []


def test_same_polarity_command_does_not_revoke():
    """"Đừng bật X" rồi "tắt X" không phải đổi ý — lệnh cấm vẫn còn hiệu lực."""
    ledger = RequirementLedger(constraints=["keep_off:den_chum_phong_khach"])
    revoke_superseded_constraints(
        ledger, _goal(action_hint="turn_off", target_device_ids=["den_chum_phong_khach"])
    )
    assert ledger.constraints == ["keep_off:den_chum_phong_khach"]


def test_constraint_on_another_device_survives():
    ledger = RequirementLedger(constraints=["keep_off:den_bep"])
    revoke_superseded_constraints(
        ledger, _goal(action_hint="turn_on", target_device_ids=["den_chum_phong_khach"])
    )
    assert ledger.constraints == ["keep_off:den_bep"]


# --- Ranh giới an toàn: ai KHÔNG được quyền lật ---------------------------------------------


def test_inferred_goal_cannot_revoke_a_user_constraint():
    """Mục tiêu suy diễn (không có action_hint/thiết bị tường minh) không phải người dùng ra lệnh."""
    ledger = RequirementLedger(constraints=["avoid:dieu_hoa_phong_khach"])
    revoked = revoke_superseded_constraints(
        ledger,
        _goal(
            utterance_type=UtteranceType.ENVIRONMENT_REQUEST,
            goal_description="làm mát thêm",
            action_hint=None,
            target_device_ids=[],
        ),
    )
    assert revoked == []
    assert ledger.constraints == ["avoid:dieu_hoa_phong_khach"]


def test_llm_guessed_grounding_cannot_revoke():
    """Ground không tất định chỉ là phỏng đoán; nó không đủ tư cách hất một ràng buộc (§4, §5.5)."""
    ledger = RequirementLedger(constraints=["keep_off:den_chum_phong_khach"])
    revoke_superseded_constraints(
        ledger,
        _goal(
            action_hint="turn_on",
            target_device_ids=["den_chum_phong_khach"],
            target_devices_deterministic=False,
        ),
    )
    assert ledger.constraints == ["keep_off:den_chum_phong_khach"]


def test_replayed_routine_cannot_revoke():
    """Routine phát lại không phải người dùng đang nói ở lượt này."""
    ledger = RequirementLedger(constraints=["keep_off:den_chum_phong_khach"])
    revoke_superseded_constraints(
        ledger,
        _goal(
            action_hint="turn_on",
            target_device_ids=["den_chum_phong_khach"],
            user_defined_routine=True,
            routine_source_event_id="evt-1",
        ),
    )
    assert ledger.constraints == ["keep_off:den_chum_phong_khach"]


def test_negated_turn_still_only_adds():
    """Lượt phủ định đặt thêm ràng buộc, không được tự gỡ ràng buộc nào."""
    store = LedgerStore()
    first = update_ledger(
        store.load("neg"),
        # `negated` là thuộc tính DẪN XUẤT từ `polarity`; gán thẳng negated=True bị bỏ qua im lặng.
        _goal(action_hint="turn_on", target_device_ids=["den_chum_phong_khach"], polarity="negative"),
        turn=1,
        conversation_id="neg",
    )
    assert "keep_off:den_chum_phong_khach" in first.constraints


# --- Hành vi quan sát được qua cả pipeline ---------------------------------------------------


def test_user_can_take_back_their_own_prohibition():
    deps, (_first, second) = _run(
        "revoke",
        "Đừng bật đèn chùm phòng khách.",
        "Bật đèn chùm phòng khách đi.",
    )
    assert second.outcome == "candidate_plan"
    assert "den_chum_phong_khach" in _devices(second)
    assert deps.ledger_store.load("revoke").constraints == []


def test_prohibition_still_holds_against_an_inferred_goal():
    """Lệnh cấm vẫn chặn đường bị cấm; agent phải tìm cách khác, không được tự gỡ."""
    deps, (_first, second) = _run(
        "hold",
        "Đừng bật đèn chùm phòng khách.",
        "Phòng khách tối quá.",
    )
    assert "den_chum_phong_khach" not in _devices(second)
    assert deps.ledger_store.load("hold").constraints == ["keep_off:den_chum_phong_khach"]


def test_a_blocked_action_says_why_instead_of_claiming_it_is_fine():
    """Không được báo "đã ở trạng thái phù hợp" khi thật ra đang bị ràng buộc chặn."""
    _deps, (_first, _second, third) = _run(
        "blocked",
        "Phòng khách tối quá.",
        "Đừng bật đèn bàn.",
        "Tăng thêm một chút nữa.",
        location="Phòng khách",
    )
    assert third.outcome == "no_action"
    assert "trạng thái phù hợp" not in third.reply
    assert "đừng bật" in third.reply
    assert "Đèn chùm" in third.reply
