"""Dựng dữ liệu SFT từ nhãn SemanticGoal đã gán tay (`semantic_goal_labels.jsonl`).

VÌ SAO KHÔNG DÙNG `expect` CỦA GOLDENSET: bản đầu của script này sinh nhãn theo một schema
4 trường tự đặt (utterance_type/intent/targets/decision). Model train xong làm tốt đúng bài
đó — nhưng pipeline KHÔNG BAO GIỜ hỏi bài đó. Node LLM thật yêu cầu nguyên một `SemanticGoal`
(action_hint, desired_outcomes[].selector/perceived_state/relative_change...). Kết quả: model
điền cả câu văn vào `action_hint` và làm sập planner. Bài học: dữ liệu huấn luyện phải khớp
ĐÚNG câu hỏi mà hệ thống sẽ hỏi lúc chạy, không phải một phiên bản đơn giản hoá của nó.

Nhãn hiện tại được gán thủ công theo đúng schema production, đã kiểm bằng
`validate_labels.py` + validator thật (0 lỗi, 0 lệch decision ngoài 3 case đã biết).

Chạy: .venv/bin/python scripts/finetune/prepare_dataset.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

LABELS_PATH = REPO_ROOT / "scripts" / "finetune" / "semantic_goal_labels.jsonl"
INPUT_PATH = REPO_ROOT / "scripts" / "finetune" / "labeling_input.jsonl"
OUT_PATH = REPO_ROOT / "scripts" / "finetune" / "sft_dataset.jsonl"

# Trường nội bộ của schema — không phải thứ model cần sinh ra. `intent` được validator tự
# điền từ goal_description nếu thiếu, nên bỏ đi để model khỏi học thuộc một nhãn thừa.
_DROP_FIELDS = ("confidence_status",)


def _system_prompt() -> str:
    """Dùng LẠI prompt vai trò của production làm system prompt.

    Train trên đúng chỉ dẫn mà lúc chạy model sẽ nhận — nếu train một đằng chạy một nẻo thì
    model phải tự tổng quát hoá qua khoảng cách đó, và đó là chỗ chất lượng rơi."""
    from src.nlu.prompting.semantic import SEMANTIC_GOAL_ROLE

    return SEMANTIC_GOAL_ROLE


def _user_content(sample: dict, catalog: str) -> str:
    parts = [f"Câu nói: {sample['utterance']}"]
    ctx = sample.get("context") or {}
    if ctx:
        parts.append(f"Ngữ cảnh: {json.dumps(ctx, ensure_ascii=False)}")
    parts.append(f"\nThiết bị trong nhà:\n{catalog}")
    return "\n".join(parts)


def _catalog_text() -> str:
    """Catalog rút gọn: slug | tên | phòng | capabilities. Model phải thấy thiết bị THẬT
    trong prompt thì mới ground được mà không bịa — giống hệt lúc chạy production."""
    from src.iot.registry import DEVICE_BY_SLUG, ROOMS

    lines = [f"Phòng: {', '.join(ROOMS)}"]
    for slug, spec in DEVICE_BY_SLUG.items():
        caps = ",".join(c.value for c in spec.capabilities)
        lines.append(f"- {slug} | {spec.name} | {spec.room} | {caps}")
    return "\n".join(lines)


def main() -> None:
    if not LABELS_PATH.exists():
        raise SystemExit(f"Chưa có nhãn: {LABELS_PATH}")

    inputs = {}
    for line in INPUT_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            inputs[r["id"]] = r

    system = _system_prompt()
    catalog = _catalog_text()

    n = 0
    with OUT_PATH.open("w", encoding="utf-8") as out:
        for line in LABELS_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            src = inputs.get(rec["id"])
            if src is None:
                continue
            goal = {k: v for k, v in rec["semantic_goal"].items() if k not in _DROP_FIELDS}
            out.write(
                json.dumps(
                    {
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": _user_content(src, catalog)},
                            {"role": "assistant", "content": json.dumps(goal, ensure_ascii=False)},
                        ],
                        "source_id": rec["id"],
                        "source_file": src.get("source_file"),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            n += 1

    size_mb = OUT_PATH.stat().st_size / 1e6
    print(f"Đã ghi {n} mẫu SFT vào {OUT_PATH} ({size_mb:.1f} MB)")
    print("Nhãn theo ĐÚNG schema SemanticGoal của production (không phải schema rút gọn).")


if __name__ == "__main__":
    main()
