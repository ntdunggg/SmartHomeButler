"""Soi một lô nhãn SemanticGoal do agent bên ngoài sinh ra, TRƯỚC khi đem đi train.

Nhãn hỏng đắt hơn nhãn thiếu: model học đúng cái sai rồi thì eval mới lộ, lúc đó không
biết lỗi do model hay do dữ liệu. Script này chặn ở cửa vào.

Kiểm 5 thứ:
1. Schema — validate bằng chính `SemanticGoal` của production (không phải mô tả lại).
2. Slug/phòng có thật — chống bịa thiết bị, lỗi tệ nhất vì model sẽ bịa theo.
3. `action_hint` thuộc tập token hợp lệ — chống điền cả câu mô tả vào (lỗi đã gặp).
4. Khớp `reference_expect` — nhãn không được mâu thuẫn với mốc người đã xác nhận.
5. Polarity so với normalizer tất định — lệch không phải là sai, nhưng đáng nhìn lại.

Chạy: .venv/bin/python scripts/finetune/validate_labels.py <file_nhan.jsonl>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

VALID_ACTION_HINTS = {"turn_on", "turn_off", "set", "increase", "decrease"}
INPUT_PATH = REPO_ROOT / "scripts" / "finetune" / "labeling_input.jsonl"


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Dùng: validate_labels.py <file_nhan.jsonl>")

    from pydantic import ValidationError

    from src.iot.registry import DEVICE_BY_SLUG, ROOMS
    from src.nlu.normalizer import analyze
    from src.nlu.schemas import SemanticGoal

    refs = {}
    if INPUT_PATH.exists():
        for line in INPUT_PATH.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                refs[r["id"]] = r

    errors: list[str] = []      # phải sửa
    warnings: list[str] = []    # nên nhìn lại
    n = 0

    for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        n += 1
        rec = json.loads(line)
        sid = rec.get("id", f"<dòng {n}>")
        raw = rec.get("semantic_goal")
        if raw is None:
            errors.append(f"{sid}: thiếu field 'semantic_goal'")
            continue

        try:
            goal = SemanticGoal.model_validate(raw)
        except ValidationError as e:
            errors.append(f"{sid}: schema không hợp lệ — {str(e)[:160]}")
            continue

        # 2. slug + phòng có thật
        for slug in goal.target_device_ids:
            if slug not in DEVICE_BY_SLUG:
                errors.append(f"{sid}: slug bịa '{slug}' (không có trong catalog)")
        if goal.target_area and goal.target_area not in ROOMS:
            errors.append(f"{sid}: phòng bịa '{goal.target_area}'")
        for i, out in enumerate(goal.desired_outcomes):
            area = getattr(out.selector, "area", None)
            if area and area not in ROOMS:
                errors.append(f"{sid}: desired_outcomes[{i}].selector.area bịa '{area}'")

        # 3. action_hint là token, không phải câu văn
        if goal.action_hint is not None and goal.action_hint not in VALID_ACTION_HINTS:
            errors.append(
                f"{sid}: action_hint='{goal.action_hint[:60]}' không thuộc {sorted(VALID_ACTION_HINTS)}"
            )

        # 4. nhất quán với mốc đã xác nhận
        ref = refs.get(sid, {})
        exp = ref.get("reference_expect") or {}
        if exp.get("utterance_type") and str(goal.utterance_type) != exp["utterance_type"]:
            errors.append(
                f"{sid}: utterance_type={goal.utterance_type} ≠ reference_expect={exp['utterance_type']}"
            )
        if "is_cancellation" in exp and goal.is_cancellation != exp["is_cancellation"]:
            errors.append(f"{sid}: is_cancellation={goal.is_cancellation} ≠ ref={exp['is_cancellation']}")
        if exp.get("targets") and set(goal.target_device_ids) != set(exp["targets"]):
            warnings.append(
                f"{sid}: targets={goal.target_device_ids} khác ref={exp['targets']}"
            )

        # 5. polarity so với normalizer tất định
        utt = ref.get("utterance") or goal.raw_utterance
        if utt:
            rule_neg = analyze(utt).has_negation
            if rule_neg != (goal.polarity == "negative"):
                warnings.append(
                    f"{sid}: polarity={goal.polarity} nhưng normalizer has_negation={rule_neg} ({utt!r})"
                )

    print(f"Đã kiểm {n} nhãn")
    print(f"  LỖI (phải sửa):    {len(errors)}")
    for e in errors:
        print("    ✗", e)
    print(f"  CẢNH BÁO (xem lại): {len(warnings)}")
    for w in warnings:
        print("    !", w)
    if not errors and not warnings:
        print("  → Sạch.")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
