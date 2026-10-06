from types import SimpleNamespace

from scripts.benchmark_planning_models import SCENARIOS, _coverage


def _planning_output(*device_ids: str) -> dict:
    return {
        "semantic_goal": SimpleNamespace(desired_outcomes=[]),
        "selected_plan": SimpleNamespace(
            actions=[SimpleNamespace(device_id=device_id) for device_id in device_ids]
        ),
    }


def test_guest_benchmark_accepts_exactly_one_shared_entertainment_device() -> None:
    scenario = next(item for item in SCENARIOS if item["name"] == "guests")
    output = _planning_output(
        "robot_hut_bui",
        "dieu_hoa_phong_khach",
        "may_loc_khong_khi_phong_khach",
        "tv_phong_khach",
    )

    coverage, issues = _coverage(output, scenario)

    assert coverage == 1.0
    assert issues == []


def test_guest_benchmark_rejects_turning_on_both_tv_and_speaker() -> None:
    scenario = next(item for item in SCENARIOS if item["name"] == "guests")
    output = _planning_output(
        "robot_hut_bui",
        "dieu_hoa_phong_khach",
        "may_loc_khong_khi_phong_khach",
        "tv_phong_khach",
        "loa_phong_khach",
    )

    coverage, issues = _coverage(output, scenario)

    assert coverage < 1.0
    assert any(issue.startswith("exactly_one_device:") for issue in issues)
