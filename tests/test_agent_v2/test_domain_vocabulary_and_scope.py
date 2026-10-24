"""Hồi quy cho bốn lỗi ngữ nghĩa tìm được khi soi 30 case goal_oriented (2026-08-31).

Kiểm theo LUẬT, không hardcode câu benchmark:
  A. `selector.domain` là từ vựng MỞ của LLM; registry là enum ĐÓNG. Chuẩn hoá phải nằm ở
     MỘT chỗ và được cả grounder, manager lẫn specialist dùng chung (§4).
  B. `target_area` cấp goal không được đè `selector.area` khi chính goal nêu nhiều phòng.
  C. Phủ định nằm gọn trong mệnh đề CẤM không biến cả mục tiêu thành phủ định — nhưng chỉ
     nhả khi đã có ràng buộc cụ thể thay thế (fail-closed).
  D. Lượng từ toàn thể ("các") là chỉ báo PHẠM VI, không phải câu thiếu phòng.
"""

from __future__ import annotations

from src.domain.enums import DeviceType, device_types_for_domain


# ---------------------------------------------------------------- A. từ vựng domain
def test_domain_chinh_xac_khong_bi_noi_rong_sang_anh_em_cung_ho() -> None:
    """"speaker" chỉ là speaker. Bảng alias cũ cho nó khớp luôn TV vì gộp chung một nhánh."""
    assert device_types_for_domain("speaker") == {DeviceType.SPEAKER.value}
    assert device_types_for_domain("tv") == {DeviceType.TV.value}


def test_domain_ho_mo_ra_dung_tap_device_type() -> None:
    assert device_types_for_domain("media_player") == {DeviceType.TV.value, DeviceType.SPEAKER.value}


def test_domain_ngoai_tu_vung_tra_rong_de_caller_fail_closed() -> None:
    """Rỗng ≠ "không ràng buộc": người gọi phải fail-closed chứ không được nới toàn bộ catalog."""
    assert device_types_for_domain("khong_ton_tai") == frozenset()
    assert device_types_for_domain(None) == frozenset()
    assert device_types_for_domain("") == frozenset()


def test_grounder_khong_con_khop_tv_khi_domain_la_speaker() -> None:
    from src.planning.device_grounder import ground_selector

    matched, _ = ground_selector({"domain": "speaker", "area": "Phòng khách"})
    slugs = {spec.slug for spec in matched}
    assert "loa_phong_khach" in slugs
    assert "tv_phong_khach" not in slugs


def test_domain_ho_van_toi_duoc_specialist_so_huu_no() -> None:
    """MediaAgent phải nhận subgoal domain="media_player": trước đây so chuỗi thô nên nó bị
    loại khỏi chính subgoal của mình và mục tiêu nghe nhạc rơi về plan rỗng."""
    from src.agent.specialists.media import MediaAgent

    owned = {device_type.value for device_type in MediaAgent().device_types}
    assert device_types_for_domain("media_player") & owned


def test_manager_suy_duoc_chieu_tu_domain_ho() -> None:
    from src.agent.planning.manager import _dimension_for_domain

    assert _dimension_for_domain("media_player") == "media"
    assert _dimension_for_domain("speaker") == "media"
    assert _dimension_for_domain("khong_ton_tai") is None


# ---------------------------------------------------------------- D. lượng từ toàn thể
def test_mao_tu_so_nhieu_la_lenh_nhom_chu_khong_phai_thieu_phong() -> None:
    """"các cửa sổ" đã nêu rõ phạm vi; hỏi lại phòng là hỏi đúng thứ người dùng vừa nói."""
    from src.agent.text import TextView
    from src.nlu.normalizer import analyze
    from src.nlu.understanding import _resolve_group_targets

    nu = analyze("đóng các cửa sổ")
    slugs = _resolve_group_targets(TextView(raw=nu.normalized, folded=nu.folded), nu, hint="close")
    assert set(slugs) == {"cua_so_phong_khach", "cua_so_phong_bo_me", "cua_so_phong_con"}


def test_mao_tu_so_nhieu_khong_kem_loai_thiet_bi_thi_khong_thanh_lenh_nhom() -> None:
    """Lượng từ một mình không đủ: "các phòng ngủ" không nêu loại nào nên không gom bừa."""
    from src.agent.text import TextView
    from src.nlu.normalizer import analyze
    from src.nlu.understanding import _resolve_group_targets

    nu = analyze("không có ai ở các phòng ngủ")
    assert _resolve_group_targets(TextView(raw=nu.normalized, folded=nu.folded), nu, hint=None) == []


# ---------------------------------------------------------------- C. phạm vi phủ định
def test_phu_dinh_gon_trong_menh_de_cam_khong_nuot_muc_tieu_chinh() -> None:
    from src.nlu.exclusion_clause import negation_is_confined_to_exclusion

    assert negation_is_confined_to_exclusion("Tôi sắp ngủ nhưng đừng tắt điều hòa")
    assert negation_is_confined_to_exclusion("Tối nay xem TV, đừng đụng vào rèm")


def test_cam_thuan_tuy_van_la_muc_tieu_phu_dinh() -> None:
    from src.nlu.exclusion_clause import negation_is_confined_to_exclusion

    assert not negation_is_confined_to_exclusion("đừng bật đèn")
    assert not negation_is_confined_to_exclusion("Không, tôi không muốn")


# ---------------------------------------------------------------- B. ưu tiên phòng
def _multi_room_goal():
    from src.agent.schemas import SemanticGoal
    from src.core.interfaces import DeviceSelector
    from src.nlu.schemas import DesiredOutcome

    return SemanticGoal(
        intent="tiết kiệm điện",
        goal_description="tắt tải không cần thiết ở các phòng ngủ",
        raw_utterance="không có ai ở các phòng ngủ, tiết kiệm điện đi",
        utterance_type="ROUTINE_INTENT",
        confidence=0.9,
        # Người nói đứng ở phòng khách — đó là VỊ TRÍ, không phải phạm vi của mục tiêu.
        target_area="Phòng khách",
        desired_outcomes=[
            DesiredOutcome(
                selector=DeviceSelector(area="Phòng ngủ bố mẹ", domain="light"),
                target_state={"power": "off"},
                cardinality="all",
            ),
            DesiredOutcome(
                selector=DeviceSelector(area="Phòng ngủ con", domain="light"),
                target_state={"power": "off"},
                cardinality="all",
            ),
        ],
    )


def test_target_area_khong_duoc_don_moi_outcome_ve_mot_phong() -> None:
    """Goal nêu HAI phòng thì một `target_area` duy nhất không thể đúng cho cả hai — đè lên
    là kéo hết về phòng người nói rồi ra plan rỗng."""
    from datetime import UTC, datetime

    from src.agent.planning.manager import build_subgoals
    from src.agent.schemas import RuntimeContext
    from src.iot.registry import ROOMS

    ctx = RuntimeContext(now=datetime(2026, 8, 31, tzinfo=UTC), rooms=list(ROOMS))
    rooms = {subgoal.room for subgoal in build_subgoals(_multi_room_goal(), ctx)}
    assert rooms == {"Phòng ngủ bố mẹ", "Phòng ngủ con"}
    assert "Phòng khách" not in rooms


def test_goal_mot_phong_van_uu_tien_target_area_da_qua_resolver() -> None:
    """Ngoại lệ chỉ áp cho goal ĐA PHÒNG: goal một phòng vẫn giữ ưu tiên cũ, để suy luận vị
    trí của model không hất được bằng chứng đã qua Semantic Resolver."""
    from datetime import UTC, datetime

    from src.agent.planning.manager import build_subgoals
    from src.agent.schemas import RuntimeContext
    from src.iot.registry import ROOMS

    goal = _multi_room_goal()
    goal = goal.model_copy(update={"desired_outcomes": goal.desired_outcomes[:1]})
    ctx = RuntimeContext(now=datetime(2026, 8, 31, tzinfo=UTC), rooms=list(ROOMS))
    assert {subgoal.room for subgoal in build_subgoals(goal, ctx)} == {"Phòng khách"}
