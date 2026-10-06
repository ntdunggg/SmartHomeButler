from src.agent.schemas import SemanticGoal


def _payload(target_parameters):
    return {
        "raw_utterance": "chuẩn bị không gian cho một hoạt động",
        "utterance_type": "ROUTINE_INTENT",
        "goal_description": "prepare the room",
        "confidence": 0.8,
        "target_parameters": target_parameters,
    }


def test_flat_untrusted_target_parameters_do_not_reject_semantic_goal() -> None:
    goal = SemanticGoal.model_validate(
        _payload({"brightness": 30, "color_temp": "warm"})
    )

    assert goal.target_parameters == {}


def test_valid_per_device_target_parameters_are_preserved() -> None:
    goal = SemanticGoal.model_validate(
        _payload({"den_chum_phong_khach": {"brightness": 30}})
    )

    assert goal.target_parameters == {
        "den_chum_phong_khach": {"brightness": 30}
    }
