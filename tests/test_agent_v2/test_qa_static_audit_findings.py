"""QA baseline (audit tĩnh 2026-08-24) — 5 phát hiện, TÁI HIỆN ĐỘC LẬP trước khi Coder sửa.

Mỗi test dưới đây pin một THUỘC TÍNH ĐÚNG mong đợi (không phải bịa theo implementation
hiện tại) và HIỆN TẠI đang ĐỎ (fail) — đây là baseline. Sau khi Coder sửa, các test này
phải XANH mà KHÔNG cần đổi assertion (chỉ được sửa lời gọi nếu chữ ký hàm production đổi).

Câu ví dụ ở đây được tác giả TỰ NGHĨ RA (không copy nguyên câu benchmark/goldenset) để
tránh học thuộc theo bộ dữ liệu — vẫn cùng HỌ ngữ nghĩa với phát hiện gốc.

Nguồn: audit tĩnh QA — xem báo cáo trong phiên trước.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from src.agent.cognitive.ledger import LedgerStore
from src.agent.harness.authorization import authorize
from src.agent.harness.validator import validate_plan
from src.agent.memory.event_store import EventStore
from src.agent.memory.profile_store import ProfileStore
from src.agent.memory.turn_store import TurnStore
from src.agent.perception.context_builder import build_perception
from src.agent.pipeline import PipelineDeps
from src.agent.preference.preference_store import PreferenceStore
from src.agent.schemas import ProposalAction
from src.agent.understanding.context_resolver import _room_from_text
from src.core.permissions import resolve_access
from src.core.reasoning import FakeReasoningModel
from src.domain.enums import DeviceType, Role
from src.domain.models import Device, User
from src.planning.grounding import resolve_single
from src.services.pipeline_bridge import reason


def _ctx():
    _, ctx, _ = build_perception("x", speaker_location="Phòng khách")
    return ctx


def _authorize_single_action(
    device_id: str, *, capability: str, action: str, risk_hint: str, role, session=None, user=None
) -> str:
    """Dựng đúng MỘT ValidatedAction cho device_id qua validate_plan thật (không tự chế field),
    rồi chạy qua authorize() — trả 'PROCEED' | 'BLOCKED' | 'WAITING_FOR_USER_APPROVAL'.

    `session`/`user` (khi truyền) là User/Session DB thật — cho authorize() đi qua
    resolve_access() per-device thay vì evaluate() tất định theo vai trò × rủi ro."""
    acts = [ProposalAction(device_id=device_id, capability=capability, action=action)]
    validated, _errors, _dropped = validate_plan(acts, _ctx())
    assert validated, f"validate_plan không tạo được action cho {device_id} — kiểm tra registry trước khi đọc kết quả"
    auth = authorize(validated, role=role, session=session, user=user)
    return auth.decision


# ---------------------------------------------------------------------------
# 1) An toàn — authorize() (agent pipeline) phải NHẤT QUÁN với resolve_access()
#    (nguồn chân lý duy nhất về quyền per-device, theo chính docstring permissions.py).
#    Test KHÔNG giả định chữ ký authorize() tương lai — chỉ đòi hỏi TÍNH NHẤT QUÁN của
#    quyết định allow/deny cho CÙNG một (role, device) giữa hai đường.
# ---------------------------------------------------------------------------

def test_authorize_agrees_with_resolve_access_for_member_without_any_grant(seeded):
    """Member CHƯA được cấp quyền thiết bị nào — resolve_access() (oracle đã có test riêng ở
    tests/test_core/test_permissions.py) từ chối NGAY CẢ với thiết bị risk NORMAL (không có
    AccessRule = coi như chưa cấp). authorize() của agent pipeline phải cho ra CÙNG kết luận
    (không PROCEED) cho đúng người + đúng thiết bị đó — hiện tại KHÔNG (authorize() luôn allow
    non-owner cho risk NORMAL bất kể có AccessRule hay không), vì nó không hề gọi resolve_access()
    (chỉ gọi evaluate() thuần theo role×risk, không đọc AccessRule/DB)."""
    member = seeded.scalar(select(User).where(User.username == "con_nho"))
    # Thiết bị NGOÀI phòng riêng của con (phòng khách) → seed không cấp sẵn → chưa có quyền.
    device = seeded.scalar(select(Device).where(Device.slug == "den_chum_phong_khach"))  # risk NORMAL
    oracle = resolve_access(seeded, user=member, device=device)
    assert oracle.allowed is False, "tiền đề: chưa cấp quyền nào thì oracle phải từ chối"

    pipeline_decision = _authorize_single_action(
        "den_chum_phong_khach",
        capability="on_off",
        action="turn_on",
        risk_hint="normal",
        role=Role.MEMBER,
        session=seeded,
        user=member,
    )
    # Kỳ vọng: agent KHÔNG được tự ý PROCEED khi oracle nói chưa cấp quyền.
    assert pipeline_decision != "PROCEED", (
        "BUG xác nhận: authorize() cho PROCEED một thiết bị risk NORMAL cho member dù "
        "resolve_access() nói CHƯA được cấp quyền — hai nguồn phân quyền không nhất quán "
        "(src/agent/harness/authorization.py chỉ gọi evaluate(), bỏ qua AccessRule)."
    )


def test_authorize_agrees_with_resolve_access_for_member_with_accepted_high_power_grant(
    seeded, grant_access
):
    """Member ĐÃ được chủ hộ cấp quyền (ACCEPTED) một thiết bị HIGH_POWER cụ thể — resolve_access()
    cho allowed=True, không cần duyệt thêm. authorize() của agent pipeline phải cho PROCEED
    (hoặc ít nhất KHÔNG BLOCKED) cho đúng thiết bị đó — hiện tại LUÔN BLOCKED cho mọi member với
    risk HIGH_POWER/SECURITY bất kể AccessRule, vì evaluate() không nhận tham số grant."""
    grant_access("con_nho", "dieu_hoa_phong_con", effect=__import__(
        "src.domain.enums", fromlist=["AccessEffect"]
    ).AccessEffect.ACCEPTED)
    member = seeded.scalar(select(User).where(User.username == "con_nho"))
    device = seeded.scalar(select(Device).where(Device.slug == "dieu_hoa_phong_con"))  # risk HIGH_POWER
    oracle = resolve_access(seeded, user=member, device=device)
    assert oracle.allowed is True and oracle.requires_approval is False, (
        "tiền đề: đã cấp ACCEPTED thì oracle phải cho phép ngay"
    )

    pipeline_decision = _authorize_single_action(
        "dieu_hoa_phong_con",
        capability="on_off",
        action="turn_on",
        risk_hint="high_power",
        role=Role.MEMBER,
        session=seeded,
        user=member,
    )
    assert pipeline_decision != "BLOCKED", (
        "BUG xác nhận: authorize() vẫn BLOCKED một thiết bị HIGH_POWER cho member dù chủ hộ đã "
        "cấp ACCEPTED qua AccessRule — vì authorize()/evaluate() không đọc AccessRule."
    )


# ---------------------------------------------------------------------------
# 2) Lệnh ghép 2 ý trái chiều trong MỘT lượt — không được gộp về một action_hint.
#    Câu tự nghĩ (khác cấu trúc benchmark "và ... và"): dùng liên từ "còn ... thì".
# ---------------------------------------------------------------------------

def _deps() -> PipelineDeps:
    return PipelineDeps(
        ledger_store=LedgerStore(), event_store=EventStore(), turn_store=TurnStore(),
        preference_store=PreferenceStore(), profile_store=ProfileStore(), model_client=FakeReasoningModel(),
    )


def _reason(msg: str, **kw):
    return reason(
        message=msg, conversation_id="qa-audit", now=datetime(2026, 8, 24, 20, 0, tzinfo=UTC),
        model_client=FakeReasoningModel(), deps=_deps(), **kw,
    )


def test_compound_turn_with_opposite_actions_must_not_collapse_to_one_action():
    """"Bật đèn chùm phòng khách lên, còn bình nóng lạnh thì tắt đi." — 2 thiết bị, 2 HÀNH ĐỘNG
    TRÁI CHIỀU trong cùng một lượt. Hiện tại toàn bộ câu chỉ ra được MỘT action_hint (dòng ưu
    tiên regex trong src/nlu/understanding.py kiểm tắt trước bật) áp cho cả hai thiết bị, nên
    đèn (đáng lẽ BẬT) lại bị TẮT sai. Test đòi đúng ngữ nghĩa: đèn phải turn_on/on, bình nóng
    lạnh phải turn_off/off — không được cùng một action."""
    r = _reason("Bật đèn chùm phòng khách lên, còn bình nóng lạnh thì tắt đi.")
    assert r.candidate_plan is not None, "phải ra được kế hoạch (có 2 thiết bị tường minh)"
    by_device = {a.device_id: a.action for a in r.candidate_plan.actions}
    assert by_device.get("den_chum_phong_khach") in ("turn_on", "open"), (
        f"BUG xác nhận: đèn phòng khách phải là bật, thực tế: {by_device}"
    )
    assert by_device.get("binh_nong_lanh") in ("turn_off", "close"), (
        f"BUG xác nhận: bình nóng lạnh phải là tắt, thực tế: {by_device}"
    )
    assert by_device.get("den_chum_phong_khach") != by_device.get("binh_nong_lanh"), (
        "hai thiết bị được yêu cầu HAI hành động trái chiều — không được ra cùng một action"
    )


# ---------------------------------------------------------------------------
# 3) Phủ định MỘT thiết bị + mệnh đề dương còn lại trong cùng lượt — phần dương
#    không được biến mất thành "answer" thuần từ chối.
#    Câu tự nghĩ, khác cấu trúc benchmark: phủ định đứng trước, phần dương là lượng từ nhóm.
# ---------------------------------------------------------------------------

def test_negation_of_one_device_must_not_swallow_positive_group_command():
    """"Đèn bàn ăn thì đừng tắt, nhưng tắt hết đèn còn lại trong bếp đi." — phần ĐẦU phủ định một
    thiết bị cụ thể (đèn bàn ăn), phần SAU là lệnh DƯƠNG cho nhóm còn lại (đèn bếp). Hiện tại
    route rơi thẳng về outcome="answer" với câu trả lời thuần "sẽ không tắt đèn bàn ăn" — mất
    hẳn phần lệnh dương (không plan, không clarify, không thực thi đèn bếp)."""
    r = _reason("Đèn bàn ăn thì đừng tắt, nhưng tắt hết đèn còn lại trong bếp đi.", focus_room="Phòng bếp")
    assert r.outcome != "answer", (
        f"BUG xác nhận: outcome='answer' (chỉ trả lời phủ định) nuốt mất mệnh đề dương "
        f"còn lại. reply={r.reply!r}"
    )
    assert r.outcome in ("candidate_plan", "clarification"), f"outcome hiện tại: {r.outcome}"
    if r.candidate_plan is not None:
        devs = [a.device_id for a in r.candidate_plan.actions]
        assert "den_ban_an" not in devs, "đèn bàn ăn bị cấm rõ ràng, không được nằm trong kế hoạch"


# ---------------------------------------------------------------------------
# 4) "phòng ngủ trẻ em" phải ground vào Phòng ngủ con (KIDS_ROOM), không bị cụm
#    "phòng ngủ" generic đã xoá làm mất định ngữ trẻ em.
# ---------------------------------------------------------------------------

def test_phong_ngu_tre_em_should_resolve_to_kids_room_not_isolated_bedroom():
    resolved = _room_from_text("phòng ngủ trẻ em hơi nóng, bật quạt máy lọc lên giúp mình")
    assert resolved == "Phòng ngủ con", (
        f"BUG xác nhận: 'phòng ngủ trẻ em' ground nhầm vào {resolved!r} thay vì "
        "'Phòng ngủ con' (nơi thật sự có đèn/điều hoà/máy lọc) — do "
        "ROOM_ALIASES khớp cụm 'phòng ngủ' trước khi xét biến thể "
        "'trẻ em' của Phòng ngủ con."
    )


# ---------------------------------------------------------------------------
# 5) resolve_single(): 0 ứng viên không được gắn nhãn "nhiều ứng viên ngang nhau".
# ---------------------------------------------------------------------------

def test_resolve_single_zero_candidates_reason_must_not_say_multiple():
    result = resolve_single(device_type=DeviceType.FAN)
    assert result.device is None
    assert result.candidates == ()
    assert result.reason != "multiple_equivalent_devices", (
        "BUG xác nhận: 0 ứng viên (nhà không có quạt) nhưng reason vẫn là "
        "'multiple_equivalent_devices' — thông điệp hỏi lại phía trên sẽ nói sai bản chất "
        "('bạn muốn cái nào?' thay vì 'nhà chưa có thiết bị này')."
    )
