"""Đóng gói MỌI thứ một agent bên ngoài cần để gán nhãn SemanticGoal, thành 1 file.

Lý do tồn tại: nhãn SemanticGoal chỉ đúng khi người gán nhãn biết ĐỒNG THỜI (a) schema
chính xác, (b) định nghĩa nhiệm vụ (prompt vai trò), và (c) catalog thiết bị THẬT — thiếu
(c) thì nhãn sẽ chứa slug/phòng bịa, huấn luyện xong model cũng bịa theo.

Chạy: .venv/bin/python scripts/finetune/build_labeling_bundle.py
Sinh ra:
  - scripts/finetune/labeling_context.md   (schema + catalog + spec nhiệm vụ)
  - scripts/finetune/labeling_input.jsonl  (319 câu cần gán nhãn, kèm ctx sẵn có)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))  # chạy trực tiếp từ scripts/ vẫn import được `src`

OUT_CONTEXT = REPO_ROOT / "scripts" / "finetune" / "labeling_context.md"
OUT_INPUT = REPO_ROOT / "scripts" / "finetune" / "labeling_input.jsonl"


def _device_catalog() -> str:
    from src.iot.registry import DEVICE_BY_SLUG, ROOMS

    lines = [f"Các phòng hợp lệ: {', '.join(ROOMS)}", "", "| slug | tên | phòng | capabilities |", "|---|---|---|---|"]
    for slug, spec in DEVICE_BY_SLUG.items():
        caps = ", ".join(c.value for c in spec.capabilities)
        lines.append(f"| `{slug}` | {spec.name} | {spec.room} | {caps} |")
    return "\n".join(lines)


def _source(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def main() -> None:
    from src.nlu.prompting.semantic import SEMANTIC_GOAL_ROLE

    context = f"""# Ngữ cảnh gán nhãn SemanticGoal — trợ lý nhà thông minh tiếng Việt

Tài liệu này chứa MỌI thứ cần để sinh nhãn `SemanticGoal` đúng cho từng câu nói.

---

## 1. Định nghĩa nhiệm vụ (prompt vai trò đang chạy trong production)

Đây là đặc tả CHÍNH XÁC của việc cần làm — nhãn sinh ra phải tuân thủ đúng tài liệu này:

```
{SEMANTIC_GOAL_ROLE}
```

---

## 2. Catalog thiết bị THẬT

Nhãn CHỈ được dùng slug/phòng có trong bảng dưới. Bịa slug = nhãn hỏng.

{_device_catalog()}

---

## 3. Schema `SemanticGoal` (Pydantic, nguồn chân lý)

Trích từ `src/nlu/schemas.py`:

```python
{_source("src/nlu/schemas.py")}
```

---

## 4. Schema nền `SemanticGoal` ở tầng domain

Trích từ `src/domain/planning.py`:

```python
{_source("src/domain/planning.py")}
```

---

## 5. Enum `UtteranceType`

Trích từ `src/nlu/ontology.py`:

```python
{_source("src/nlu/ontology.py")}
```

---

## 6. Enum `Capability` / `DeviceType`

Trích từ `src/domain/enums.py`:

```python
{_source("src/domain/enums.py")}
```
"""
    OUT_CONTEXT.write_text(context, encoding="utf-8")

    # Gom câu cần gán nhãn từ goldenset. Giữ lại `expect` cũ làm THAM CHIẾU (utterance_type,
    # decision, targets đã được con người xác nhận) — người gán nhãn phải nhất quán với nó,
    # chỉ bổ sung phần SemanticGoal còn thiếu.
    datasets_dir = REPO_ROOT / "src" / "evaluation" / "datasets"
    n = 0
    with OUT_INPUT.open("w", encoding="utf-8") as out:
        for f in sorted(datasets_dir.glob("*.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                sample = json.loads(line)
                if "utterance" not in sample:
                    continue
                out.write(
                    json.dumps(
                        {
                            "id": sample.get("id"),
                            "utterance": sample["utterance"],
                            "context": sample.get("context") or {},
                            "reference_expect": sample.get("expect") or {},
                            "source_file": f.name,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                n += 1

    print(f"Đã ghi {OUT_CONTEXT} ({OUT_CONTEXT.stat().st_size // 1024} KB)")
    print(f"Đã ghi {OUT_INPUT} ({n} câu cần gán nhãn)")


if __name__ == "__main__":
    main()
