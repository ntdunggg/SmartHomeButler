"""Tám nguyên tắc thiết kế system prompt, viết thành test.

Prompt là code có version, nhưng khác code ở chỗ nó KHÔNG tự vỡ khi sai: một prompt tự mâu
thuẫn vẫn chạy, chỉ cho kết quả tệ hơn một cách im lặng. Nên các nguyên tắc dưới đây phải có
một chốt kiểm tra tự động, nếu không chúng trôi — và chúng ĐÃ trôi:

- `principles` bảo "đừng từ chối cho chắc" trong khi `semantic` bảo "thà không hành động",
  hai lập trường ngược nhau nằm ở hai file (nguyên tắc 5);
- `planner` cấm TUYỆT ĐỐI mọi thiết bị an ninh trong khi `validator.validate_plan` cố ý CHO PHÉP
  hành động khoá từ mục tiêu suy diễn, kèm cờ chờ duyệt (nguyên tắc 7);
- `semantic` tự mâu thuẫn với chính nó về việc khoá cửa trong một nếp sinh hoạt.

Các test ở đây kiểm CẤU TRÚC và NGÂN SÁCH của prompt, không kiểm chất lượng suy luận của model
(việc đó thuộc goldenset/eval live). Chúng là chốt chặn hồi quy, không phải thước đo chất lượng.
"""

from __future__ import annotations

import pytest

from src.nlu import prompting as pr
from src.nlu.prompts import INJECTION_DEFENSE

_COMPOSED = {
    name: getattr(pr, name) for name in dir(pr) if name.endswith("_SYSTEM_PROMPT")
}


def _flat(text: str) -> str:
    """Gộp khoảng trắng trước khi so khớp: prompt được wrap thủ công nên một cụm từ có thể bị
    ngắt qua hai dòng ("(mở\n   khoá...)"). Assert trên chuỗi thô sẽ trượt vì lý do trình bày,
    không phải vì nội dung sai."""
    return " ".join(text.split())


# --- Nguyên tắc 1 & 2: vai trò rõ, phạm vi có giới hạn --------------------------------
def test_principles_state_the_role_and_its_limit() -> None:
    text = _flat(pr.AGENT_PRINCIPLES).lower()
    assert "nhà thông minh" in text
    assert "không phải trợ" in text, "phải nêu cả vế phủ định: không phải trợ lý đa năng"
    assert "phạm vi" in text


@pytest.mark.parametrize("name", sorted(_COMPOSED))
def test_every_node_inherits_role_and_authority(name: str) -> None:
    """Mọi system prompt = principles + (persona) + vai trò. Không node nào được tự do
    khỏi hiến pháp: một node quên ranh giới thẩm quyền là một node tưởng mình được quyết."""
    assert _COMPOSED[name].startswith(pr.AGENT_PRINCIPLES)


# --- Nguyên tắc 4: LLM đề xuất, không quyết ------------------------------------------
def test_authority_boundary_is_stated_once_and_clearly() -> None:
    text = _flat(pr.AGENT_PRINCIPLES)
    assert "ĐỀ XUẤT" in text and "QUYẾT ĐỊNH" in text
    assert "KHÔNG thực thi" in text


# --- Nguyên tắc 5: an toàn đi trước, nhưng đúng trường -------------------------------
def test_the_abstain_rule_is_stated_as_one_reconciled_rule() -> None:
    """Vế "hiểu dứt khoát" và vế "hành động dè dặt" phải đứng CẠNH NHAU trong hiến pháp.

    Tách chúng ra hai file là cách chúng đã trôi thành hai lập trường đối nghịch."""
    text = _flat(pr.AGENT_PRINCIPLES)
    assert "HIỂU thì dứt khoát" in text
    assert "HÀNH ĐỘNG thì dè dặt" in text
    assert "không mâu thuẫn" in text, "phải nói rõ vì sao hai vế cùng đúng"


def test_no_prompt_tells_the_model_to_refuse_when_it_understood() -> None:
    """Không prompt nào được dạy model bỏ nhãn phân loại vì thấy câu mơ hồ."""
    for name, text in _COMPOSED.items():
        flat = _flat(text)
        assert "mơ hồ" not in flat or "HIỂU thì dứt khoát" in flat, name


# --- Nguyên tắc 7: policy do CODE thi hành, prompt chỉ mô tả -------------------------
def test_the_security_rule_in_the_prompt_matches_what_the_code_enforces() -> None:
    """`validate_plan` cố ý cho phép hành động KHOÁ từ mục tiêu suy diễn (kèm cờ chờ duyệt) và
    loại mọi hướng còn lại. Prompt planner phải mô tả ĐÚNG luật đó.

    Prompt cấm rộng hơn code không "an toàn hơn": nó chỉ khiến planner bỏ mất đúng bước người
    dùng mong đợi nhất khi rời nhà, còn phần chặn thật vẫn do code làm."""
    from src.agent.harness.validator import validate_plan
    from src.agent.schemas import ProposalAction

    plan_prompt = _flat(pr.CANDIDATE_PLAN_SYSTEM_PROMPT)
    assert "TUYỆT ĐỐI KHÔNG chạm thiết bị an ninh" not in plan_prompt
    assert "hành động KHOÁ" in plan_prompt, "phải nêu ngoại lệ khoá mà code cho phép"
    assert "mở khoá, tắt/bật camera) thì KHÔNG đề xuất" in plan_prompt

    # Và hành vi code đúng như prompt vừa mô tả.
    from datetime import UTC, datetime

    from src.agent.schemas import RuntimeContext

    ctx = RuntimeContext(now=datetime(2026, 8, 29, tzinfo=UTC), rooms=["Phòng khách"])
    lock = [ProposalAction(device_id="khoa_cua_chinh", capability="lock", action="lock")]
    unlock = [ProposalAction(device_id="khoa_cua_chinh", capability="lock", action="unlock")]

    locked, _, dropped_lock = validate_plan(lock, ctx, is_inferred_goal=True)
    _, _, dropped_unlock = validate_plan(unlock, ctx, is_inferred_goal=True)

    assert dropped_lock == [] and [a.requires_confirmation for a in locked] == [True]
    assert dropped_unlock == ["khoa_cua_chinh"]


def test_a_prompt_never_claims_to_be_the_permission_check() -> None:
    """Prompt được phép NHẮC luật, không được tự nhận mình là nơi thi hành luật."""
    for name, text in _COMPOSED.items():
        lowered = _flat(text).lower()
        if "an ninh" in lowered:
            assert "lớp tất định" in lowered or "validator" in lowered or "policy" in lowered, (
                f"{name}: nhắc luật an ninh thì phải nói rõ ai thi hành nó"
            )


# --- Nguyên tắc 6 & 8: output rõ, prompt ngắn, không lặp -----------------------------
@pytest.mark.parametrize("name", sorted(_COMPOSED))
def test_every_prompt_pins_its_output_contract(name: str) -> None:
    assert "JSON" in _COMPOSED[name], name


def test_the_shared_blocks_do_not_repeat_each_other() -> None:
    """Hiến pháp, persona và đoạn phòng thủ injection ghép vào MỌI lời gọi. Mỗi câu lặp là
    token trả tiền ở mọi lượt, và là một chỗ nữa để hai bản trôi lệch nhau."""
    assert "Chỉ trả JSON" not in INJECTION_DEFENSE, "AGENT_PRINCIPLES đã nói rồi"
    assert "bịa thiết bị" not in INJECTION_DEFENSE, "AGENT_PRINCIPLES đã nói rồi"
    assert "ĐỀ XUẤT" not in pr.ASSISTANT_PERSONA, "ranh giới thẩm quyền thuộc về principles"


# Trần ngân sách token (ước lượng thô ~3 byte/token cho tiếng Việt có dấu). Đây là RATCHET:
# đặt sát mức hiện tại để prompt không âm thầm phình ra. Muốn vượt trần thì phải sửa số ở đây,
# và lúc đó là một quyết định có ý thức kèm eval, không phải một dòng thêm vào cho chắc.
_BUDGET_TOKENS = {
    "AGENT_PRINCIPLES": 700,
    "ASSISTANT_PERSONA": 350,
    "INJECTION_DEFENSE": 300,
}


@pytest.mark.parametrize("name,cap", sorted(_BUDGET_TOKENS.items()))
def test_shared_blocks_stay_within_budget(name: str, cap: int) -> None:
    text = INJECTION_DEFENSE if name == "INJECTION_DEFENSE" else getattr(pr, name)
    assert len(text) // 3 <= cap, f"{name} ~{len(text) // 3} token, trần {cap}"


def test_the_semantic_role_bloat_is_visible_and_bounded() -> None:
    """SEMANTIC_GOAL_ROLE là prompt dài nhất hệ thống (~4.3k token, gấp ~9 lần hiến pháp).

    Phần lớn độ dài đến từ các đoạn liệt kê nhóm thiết bị cho từng loại sự kiện — tích tụ qua
    nhiều vòng vá eval. Nó vi phạm nguyên tắc 8, nhưng cắt nó là thay đổi HÀNH VI LLM: phải đo
    bằng eval live trước/sau, không phải bằng test đơn vị. Trần dưới đây chỉ chặn nó phình
    THÊM, và để con số ở chỗ ai cũng thấy thay vì giấu trong một file 200 dòng."""
    from src.nlu.prompting.semantic import SEMANTIC_GOAL_ROLE

    assert len(SEMANTIC_GOAL_ROLE) // 3 <= 4400, "muốn thêm thì phải cắt chỗ khác, hoặc có eval"


def test_nep_ve_nha_phu_du_bon_dieu_kien_san_sang() -> None:
    """Nếp "về nhà" phải phủ ĐỦ bốn điều kiện, trong đó có vệ sinh sàn.

    Trước 2026-08-30 spec chỉ nêu BA điều kiện (sáng/nhiệt độ/không khí) và còn chủ động loại
    vệ sinh ra: "việc vệ sinh đang chạy có thể cần dừng". Đo live 3 lần: vacuum 0/3. Luật dừng
    đó hợp cho "đã về tới nơi" nhưng bị áp nhầm cho "sắp về" — lúc còn thời gian để robot chạy
    xong trước khi người tới. Sau khi sửa: robot_hut_bui turn_on 3/3 (đo ở cả 01:00 lẫn 19:00).
    """
    from src.nlu.prompting.semantic import SEMANTIC_GOAL_ROLE

    assert "BỐN điều kiện sẵn sàng" in SEMANTIC_GOAL_ROLE
    assert 'vacuum power="on"' in SEMANTIC_GOAL_ROLE
    assert "vệ sinh đang chạy có thể cần dừng" not in SEMANTIC_GOAL_ROLE


def test_dieu_kien_san_sang_khong_bi_luat_cam_bien_chan() -> None:
    """Hai luật từng mâu thuẫn: bốn điều kiện là bắt buộc, nhưng luật sau lại cấm thêm outcome
    nhiệt độ/không khí khi thiếu bằng chứng cảm biến. Mâu thuẫn đó đo được: cùng một câu ra
    3/1/0 outcomes. Ngoại lệ phải nêu TƯỜNG MINH, không để model tự xử."""
    from src.nlu.prompting.semantic import SEMANTIC_GOAL_ROLE

    assert "TRỪ bốn" in SEMANTIC_GOAL_ROLE, "phải nói rõ điều kiện sẵn sàng không bị luật này chặn"


def test_social_activity_uses_semantic_alternatives_instead_of_two_media_commands() -> None:
    from src.nlu.prompting.semantic import SEMANTIC_GOAL_ROLE

    prompt = " ".join(SEMANTIC_GOAL_ROLE.split())
    prompt_lower = prompt.lower()
    assert prompt.rfind("KIỂM TRA CUỐI") > prompt.find("VÍ DỤ MINH HOẠ")
    assert 'activity_context="socializing"' in prompt
    assert "PHẢI có ĐỦ BỐN" in prompt
    assert 'domain="media_player"' in prompt
    assert 'labels=["shared_entertainment"]' in prompt
    assert 'cardinality="any"' in prompt
    assert "không tạo hai outcomes riêng" in prompt_lower
    assert 'temperature="decrease"' in prompt
    assert "air-quality/air-flow" in prompt
    assert "Không dùng ánh sáng để thay thế" in prompt
