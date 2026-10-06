"""Release gate must remain external, blind, isolated, and fail closed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.spec_v2_acceptance import AcceptanceConfigurationError, validate_external_dataset


def _case(case_id: str) -> dict:
    return {
        "id": case_id,
        "category": "unseen",
        "turns": [{"utterance": "Một cách diễn đạt mù", "expect": ["clarification"]}],
    }


def _write_jsonl(path: Path, count: int) -> None:
    path.write_text(
        "\n".join(json.dumps(_case(f"blind-{i}"), ensure_ascii=False) for i in range(count)),
        encoding="utf-8",
    )


def test_acceptance_rejects_repository_local_dataset():
    local = Path(__file__).resolve().parents[2] / "src/evaluation/goldensets/goldenset_ambiguity.jsonl"
    with pytest.raises(AcceptanceConfigurationError, match="outside the repository"):
        validate_external_dataset(local, min_cases=1)


def test_acceptance_validates_external_dataset_contract(tmp_path):
    external = tmp_path / "blind.jsonl"
    _write_jsonl(external, 3)
    resolved, cases = validate_external_dataset(external, min_cases=3)
    assert resolved == external.resolve()
    assert len(cases) == 3


def test_acceptance_enforces_minimum_size(tmp_path):
    external = tmp_path / "too-small.jsonl"
    _write_jsonl(external, 2)
    with pytest.raises(AcceptanceConfigurationError, match="at least 3"):
        validate_external_dataset(external, min_cases=3)
