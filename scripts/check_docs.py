"""Kiểm tra tài liệu đi kèm thay đổi code (chạy trước commit và trong ``make docs-check``).

Ba quy tắc:
1. Thay đổi trong ``src/``, ``evals/``, ``frontend/``, ``config/`` phải kèm thay đổi
   ``docs/CHANGELOG.md`` trong cùng commit (hoặc cùng nhánh khi dùng ``--base``).
2. ``docs/CHANGELOG.md`` luôn có mục ``## [Chưa phát hành]``.
3. Mỗi ADR trong ``docs/decisions/`` có đủ trạng thái, ngày, các mục bắt buộc và có tên
   trong bảng của ``docs/decisions/README.md``.
4. Mỗi file lỗi ``docs/findings/NN-*.md`` có ngày ghi nhận, mức, trạng thái và có tên trong
   sổ lỗi ``docs/findings/README.md``; mã trong sổ lỗi không trùng nhau.

Bỏ qua quy tắc 1 cho một commit thật sự không cần ghi (ví dụ chỉ sửa chính tả trong
comment): ``SKIP_DOCS_CHECK=1 git commit ...``. Lần bỏ qua được in ra để người commit tự chịu.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = "docs/CHANGELOG.md"
DECISIONS_DIR = "docs/decisions"
CODE_PREFIXES = ("src/", "evals/", "frontend/", "config/")
CODE_EXCLUDES = ("evals/results/", "evals/holdout_log.jsonl")
ADR_STATUSES = ("Đề xuất", "Đã chấp nhận", "Bị thay thế", "Huỷ")
ADR_SECTIONS = ("## Bối cảnh", "## Quyết định", "## Hệ quả")
ADR_NAME = re.compile(r"^(\d{4})-[a-z0-9-]+\.md$")
FINDINGS_DIR = "docs/findings"
FINDING_NAME = re.compile(r"^(\d{2})-[a-z0-9-]+\.md$")
FINDING_LEVELS = ("Cao", "Chặn", "Trung bình", "Thấp")


def check_changed(paths: list[str]) -> list[str]:
    code = [p for p in paths if p.startswith(CODE_PREFIXES) and not p.startswith(CODE_EXCLUDES)]
    if code and CHANGELOG not in paths:
        shown = ", ".join(code[:5]) + (" …" if len(code) > 5 else "")
        return [
            f"Có thay đổi code ({shown}) nhưng chưa ghi vào {CHANGELOG}. "
            "Thêm một dòng vào mục [Chưa phát hành]: cái gì đổi và vì sao. "
            "Nếu đổi hướng đi, viết thêm ADR trong docs/decisions/."
        ]
    return []


def check_changelog(text: str) -> list[str]:
    if "## [Chưa phát hành]" not in text:
        return [f"{CHANGELOG} thiếu mục '## [Chưa phát hành]'."]
    return []


def check_adrs(decisions_dir: Path) -> list[str]:
    errors: list[str] = []
    index_path = decisions_dir / "README.md"
    index = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
    for path in sorted(decisions_dir.glob("*.md")):
        if path.name == "README.md":
            continue
        match = ADR_NAME.match(path.name)
        if not match:
            errors.append(f"{path.name}: tên ADR phải dạng NNNN-ten-ngan.md (chữ thường, không dấu).")
            continue
        number = match.group(1)
        if number == "0000":  # file mẫu
            continue
        text = path.read_text(encoding="utf-8")
        status = re.search(r"^- \*\*Trạng thái:\*\* (.+)$", text, re.MULTILINE)
        if not status or not status.group(1).strip().startswith(ADR_STATUSES):
            errors.append(f"{path.name}: thiếu hoặc sai '- **Trạng thái:**' (một trong: {', '.join(ADR_STATUSES)}).")
        if not re.search(r"^- \*\*Ngày:\*\* \d{4}-\d{2}-\d{2}\s*$", text, re.MULTILINE):
            errors.append(f"{path.name}: thiếu '- **Ngày:** YYYY-MM-DD'.")
        errors.extend(f"{path.name}: thiếu mục '{s}'." for s in ADR_SECTIONS if s not in text)
        if f"({path.name})" not in index:
            errors.append(f"{path.name}: chưa có trong bảng của {DECISIONS_DIR}/README.md.")
    return errors


def check_findings(findings_dir: Path) -> list[str]:
    errors: list[str] = []
    register_path = findings_dir / "README.md"
    if not register_path.exists():
        return [f"Thiếu sổ lỗi {FINDINGS_DIR}/README.md."]
    register = register_path.read_text(encoding="utf-8")
    codes = re.findall(r"^\| (\d{2}) \|", register, re.MULTILINE)
    dupes = sorted({c for c in codes if codes.count(c) > 1})
    if dupes:
        errors.append(f"Sổ lỗi có mã trùng: {', '.join(dupes)}.")
    for path in sorted(findings_dir.glob("*.md")):
        if path.name == "README.md":
            continue
        match = FINDING_NAME.match(path.name)
        if not match:
            errors.append(f"{path.name}: tên file lỗi phải dạng NN-ten-ngan.md (chữ thường, không dấu).")
            continue
        if match.group(1) == "00":  # file mẫu
            continue
        text = path.read_text(encoding="utf-8")
        if not re.search(r"^- \*\*Ngày ghi nhận:\*\* \d{4}-\d{2}-\d{2}", text, re.MULTILINE):
            errors.append(f"{path.name}: thiếu '- **Ngày ghi nhận:** YYYY-MM-DD'.")
        level = re.search(r"^- \*\*Mức:\*\* (.+)$", text, re.MULTILINE)
        if not level or not level.group(1).strip().startswith(FINDING_LEVELS):
            errors.append(f"{path.name}: thiếu hoặc sai '- **Mức:**' (một trong: {', '.join(FINDING_LEVELS)}).")
        if not re.search(r"^- \*\*Trạng thái:\*\* \S", text, re.MULTILINE):
            errors.append(f"{path.name}: thiếu '- **Trạng thái:**'.")
        if match.group(1) not in codes:
            errors.append(f"{path.name}: mã {match.group(1)} chưa có dòng trong sổ lỗi {FINDINGS_DIR}/README.md.")
        elif f"({path.name})" not in register:
            errors.append(f"{path.name}: dòng {match.group(1)} trong sổ lỗi chưa dẫn link tới file này.")
    return errors


def _git_paths(*args: str) -> list[str]:
    out = subprocess.run(["git", *args], capture_output=True, text=True, check=True, cwd=ROOT).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--staged", action="store_true", help="Kiểm tra các file đang staged (dùng trong pre-commit)")
    group.add_argument("--base", help="Kiểm tra mọi thay đổi từ nhánh gốc, ví dụ --base main")
    args = parser.parse_args(argv)

    errors = (
        check_changelog((ROOT / CHANGELOG).read_text(encoding="utf-8"))
        + check_adrs(ROOT / DECISIONS_DIR)
        + check_findings(ROOT / FINDINGS_DIR)
    )
    changed: list[str] = []
    if args.staged:
        changed = _git_paths("diff", "--cached", "--name-only", "--diff-filter=ACMRD")
    elif args.base:
        changed = _git_paths("diff", "--name-only", f"{args.base}...HEAD")
    if changed:
        changelog_errors = check_changed(changed)
        if changelog_errors and os.environ.get("SKIP_DOCS_CHECK") == "1":
            print("Bỏ qua yêu cầu ghi CHANGELOG theo SKIP_DOCS_CHECK=1.", file=sys.stderr)
        else:
            errors += changelog_errors

    if errors:
        print("Kiểm tra tài liệu chưa đạt:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
