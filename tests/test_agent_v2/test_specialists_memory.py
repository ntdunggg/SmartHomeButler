"""QC-06 (§35): Memory Evidence vào planning — profile_evidence là PRIOR target số, tách RL (P5)."""

from __future__ import annotations

from src.agent.perception.context_builder import build_perception
from src.agent.planning.manager import Subgoal
from src.agent.schemas import PreferenceDistribution
from src.agent.specialists.ac import ACAgent
from src.agent.specialists.lighting import LightingAgent
from src.agent.specialists.registry import memory_target

_PROFILE_LIGHT = [{"subject": "den_chum_phong_khach", "fact": "preferred_brightness", "value": {"value": 35}}]
_PROFILE_AC = [{"subject": "dieu_hoa_phong_khach", "fact": "preferred_temperature", "value": {"value": 22}}]


def _ctx():
    _nu, ctx, _p = build_perception("cho sáng lên", speaker_location="Phòng khách")
    return ctx


def _light_sub():
    return Subgoal(dimension="illumination", direction="increase", room="Phòng khách")


def _ac_sub():
    return Subgoal(dimension="temperature", direction="increase", room="Phòng khách")


def _brightness_target(prop):
    a = next(a for a in prop.actions if a.device_id == "den_chum_phong_khach")
    return a.target["brightness"]


def test_memory_target_matches_subject_relation():
    assert memory_target(_PROFILE_LIGHT, "den_chum_phong_khach", "preferred_brightness") == 35
    assert memory_target(_PROFILE_LIGHT, "den_bep", "preferred_brightness") is None  # khác thiết bị
    assert memory_target(_PROFILE_LIGHT, "den_chum_phong_khach", "preferred_temperature") is None  # khác relation
    assert memory_target(None, "den_chum_phong_khach", "preferred_brightness") is None


def test_lighting_uses_profile_prior_when_no_rl():
    """RL chưa học (không preference) → dùng prior ký ức ổn định (§24) làm target."""
    prop = LightingAgent().propose(_light_sub(), _ctx(), profile=_PROFILE_LIGHT)
    assert _brightness_target(prop) == 35
    assert any("memory:preferred_brightness=35" in e for e in prop.evidence)  # provenance rõ ràng


def test_ac_uses_profile_prior_when_no_rl():
    prop = ACAgent().propose(_ac_sub(), _ctx(), profile=_PROFILE_AC)
    a = next(a for a in prop.actions if a.device_id == "dieu_hoa_phong_khach")
    assert a.target["temperature"] == 22
    assert any("memory:preferred_temperature=22" in e for e in prop.evidence)


def test_rl_preference_wins_over_profile_memory():
    """P5: RL đã học (confidence>0) THẮNG prior ký ức — memory chỉ là prior khi RL chưa có."""
    pref = {"brightness": PreferenceDistribution(dimension="brightness", distribution={"60": 0.9, "70": 0.1}, confidence=0.8)}
    prop = LightingAgent().propose(_light_sub(), _ctx(), preference=pref, profile=_PROFILE_LIGHT)
    assert _brightness_target(prop) == 60  # RL, không phải 35


def test_explicit_value_wins_over_memory():
    """Người dùng nêu số cụ thể → thắng cả RL lẫn memory."""
    sub = Subgoal(dimension="illumination", direction="increase", room="Phòng khách", target_state={"brightness": 80})
    prop = LightingAgent().propose(sub, _ctx(), profile=_PROFILE_LIGHT)
    assert _brightness_target(prop) == 80


def test_no_profile_falls_back_to_heuristic():
    """Không có profile + không RL → nhánh heuristic cũ (không đổi hành vi khi chưa có ký ức)."""
    prop = LightingAgent().propose(_light_sub(), _ctx(), profile=None)
    assert _brightness_target(prop) == 100  # heuristic max(current 70 +30, 70) → clamp 100, KHÔNG phải 35 (memory)
    assert not any("memory:" in e for e in prop.evidence)
