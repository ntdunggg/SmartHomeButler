"""Kiểm tra tài liệu đi kèm thay đổi (scripts/check_docs.py), và tài liệu THẬT của repo đạt chuẩn."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_docs", ROOT / "scripts" / "check_docs.py")
assert _spec and _spec.loader
check_docs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_docs)

VALID_ADR = """# ADR-0001: X

- **Trạng thái:** Đã chấp nhận
- **Ngày:** 2026-11-08

## Bối cảnh
a
## Quyết định
b
## Hệ quả
c
"""


def test_code_change_without_changelog_is_rejected():
    assert check_docs.check_changed(["src/agent/pipeline.py"])
    assert check_docs.check_changed(["evals/run.py", "README.md"])


def test_code_change_with_changelog_and_doc_only_changes_pass():
    assert check_docs.check_changed(["src/agent/pipeline.py", "docs/CHANGELOG.md"]) == []
    assert check_docs.check_changed(["README.md", "docs/roadmap.md", "tests/test_x.py"]) == []
    assert check_docs.check_changed(["evals/results/x/report.md", "evals/holdout_log.jsonl"]) == []


def test_changelog_needs_unreleased_section():
    assert check_docs.check_changelog("# Nhật ký\n## [Mốc 0]\n")
    assert check_docs.check_changelog("## [Chưa phát hành]\n") == []


def _write(dir_: Path, name: str, text: str) -> None:
    (dir_ / name).write_text(text, encoding="utf-8")


def test_adr_must_be_complete_and_indexed(tmp_path):
    _write(tmp_path, "README.md", "| [0001](0001-x.md) |")
    _write(tmp_path, "0000-mau.md", "mẫu không cần đủ mục")
    _write(tmp_path, "0001-x.md", VALID_ADR)
    assert check_docs.check_adrs(tmp_path) == []

    _write(tmp_path, "0002-y.md", VALID_ADR.replace("Đã chấp nhận", "Tạm").replace("## Hệ quả", ""))
    errors = check_docs.check_adrs(tmp_path)
    assert any("Trạng thái" in e for e in errors)
    assert any("Hệ quả" in e for e in errors)
    assert any("chưa có trong bảng" in e for e in errors)


def test_repository_docs_follow_the_rules():
    """Tài liệu thật của repo: CHANGELOG có mục chưa phát hành, mọi ADR đủ chuẩn."""
    changelog = (ROOT / "docs" / "CHANGELOG.md").read_text(encoding="utf-8")
    assert check_docs.check_changelog(changelog) == []
    assert check_docs.check_adrs(ROOT / "docs" / "decisions") == []
