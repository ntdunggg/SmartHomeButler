"""CLI for deterministic 10/20/50-turn ledger evaluation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.evaluation.long_context_ledger import run_long_context_eval


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()

    report = run_long_context_eval()
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.json_out is not None:
        args.json_out.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

