"""Tổng hợp 4 role × 100 request → phân bố outcome + khác biệt quyền theo role.

Chạy: python src/evaluation/results/role_permission_eval/aggregate.py
Đọc 4 file kết quả nằm cùng thư mục (đã cố định, không phụ thuộc scratchpad tạm).
"""
from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
# username → file (theo vai trò/độ tuổi)
FILES = {
    "bo": "bo_owner_adult.json",
    "me": "me_owner_adult.json",
    "con_lon": "con_lon_member_teen.json",
    "con_nho": "con_nho_member_child.json",
}
USERS = list(FILES)
OUTCOMES = ["executed", "approval", "blocked", "noop", "clarification", "answered", "cancelled", "no_goal", "abstain", "error"]

data = {u: json.loads((HERE / FILES[u]).read_text()) for u in USERS}


def label(u: str) -> str:
    d = data[u]
    return f"{u} ({d['role']})"


lines: list[str] = []


def p(s: str = "") -> None:
    print(s)
    lines.append(s)


p("=" * 92)
p("PHÂN BỐ OUTCOME THEO ROLE  (100 request / role, LLM thật từ MODEL_NAME)")
p("=" * 92)
p("outcome".ljust(14) + "".join(u.rjust(11) for u in USERS))
p("-" * 92)
for oc in OUTCOMES:
    row = oc.ljust(14)
    for u in USERS:
        c = sum(1 for r in data[u]["results"] if r["outcome"] == oc)
        row += (str(c) if c else "·").rjust(11)
    p(row)
p("-" * 92)
for u in USERS:
    lats = [r["latency_ms"] for r in data[u]["results"]]
    p(f"{label(u)}: median {int(statistics.median(lats))}ms  p95 {int(sorted(lats)[94])}ms  max {max(lats)}ms")

p("")
p("=" * 92)
p("KHÁC BIỆT QUYỀN THEO ROLE — cùng request, khác kết quả")
p("=" * 92)
base = data["bo"]["results"]
for cat_filter, title in [
    ("security", "AN NINH (khoá cửa / camera)"),
    ("climate", "CÔNG SUẤT LỚN (điều hoà/sưởi/bình nóng)"),
    ("appliance", "GIA DỤNG (gồm high-power)"),
]:
    p("")
    p(f"### {title}")
    for i, r in enumerate(base):
        if r["category"] != cat_filter:
            continue
        cells = [f"{u.split('_')[-1][:4]}:{data[u]['results'][i]['outcome'][:4]}" for u in USERS]
        p(f"  {r['request'][:42].ljust(42)} | " + "  ".join(cells))

p("")
p("=" * 92)
p("NLU TỔNG QUÁT (role bo/OWNER) — outcome theo category")
p("=" * 92)
byc: dict[str, Counter] = {}
for r in data["bo"]["results"]:
    byc.setdefault(r["category"], Counter())[r["outcome"]] += 1
for cat in sorted(byc):
    p(f"  {cat.ljust(13)} " + ", ".join(f"{k}:{v}" for k, v in byc[cat].most_common()))

p("")
p("=" * 92)
p("LỖI / ABSTAIN")
p("=" * 92)
any_err = False
for u in USERS:
    for r in data[u]["results"]:
        if r["outcome"] in ("error", "abstain"):
            any_err = True
            p(f"  [{u}] {r['request'][:40]} → {r['outcome']} {r.get('error', '')}")
if not any_err:
    p("  (không có lỗi/abstain nào)")

reused = sum(1 for u in USERS for r in data[u]["results"] if r.get("reused_routine"))
p("")
p(f"reused_routine = True: {reused} (kỳ vọng 0 — phiên eval sạch, chưa học routine)")

(HERE / "REPORT.txt").write_text("\n".join(lines) + "\n")
print(f"\n→ đã ghi {HERE / 'REPORT.txt'}")
