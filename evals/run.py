"""Chạy dataset qua pipeline thật và ghi kết quả theo giao thức trong ``protocol.py``.

Ví dụ::

    # Tập phát triển, chỉ luật (không tốn lượt gọi API), 3 lần
    .venv/bin/python -m evals.run evals/datasets/smoke.jsonl --split dev --mode rules

    # Tập phát triển với LLM, cách nhau 4 giây giữa các lượt có gọi model
    .venv/bin/python -m evals.run evals/datasets/smoke.jsonl --split dev --mode llm --min-interval 4

    # Hết quota giữa chừng → chạy tiếp đúng chỗ dừng
    .venv/bin/python -m evals.run evals/datasets/smoke.jsonl --resume evals/results/<thư-mục>

    # Holdout: worktree sạch, có lý do, mỗi phiên bản dataset một lần
    .venv/bin/python -m evals.run evals/datasets/smoke.jsonl --split holdout --mode llm \
        --reason "Số liệu cuối cho báo cáo"
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from evals import protocol

ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"
HOLDOUT_LOG = ROOT / "holdout_log.jsonl"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m evals.run", description="Đo agent VinButler theo giao thức khách quan.")
    p.add_argument("dataset", type=Path)
    p.add_argument("--split", choices=["dev", "holdout", "all"], default="dev")
    p.add_argument("--mode", choices=["rules", "llm"], default="rules",
                   help="rules = không gọi model (như khi LLM tắt); llm = model cấu hình trong .env")
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--holdout-pct", type=int, default=protocol.DEFAULT_HOLDOUT_PCT)
    p.add_argument("--min-interval", type=float, default=0.0,
                   help="Số giây chờ sau mỗi lượt CÓ gọi model (giữ dưới giới hạn gói miễn phí)")
    p.add_argument("--no-embedding", action="store_true", help="Tắt embedding API (tiết kiệm quota)")
    p.add_argument("--limit", type=int, default=0, help="Chỉ chạy N ca đầu (thử nhanh, không dùng cho số liệu báo cáo)")
    p.add_argument("--resume", type=Path, help="Thư mục kết quả cũ để chạy tiếp")
    p.add_argument("--reason", default="", help="Bắt buộc khi --split holdout")
    p.add_argument("--rerun-holdout", action="store_true", help="Cho phép chạy lại holdout (vẫn bị ghi sổ)")
    args = p.parse_args(argv)
    if args.runs < 1:
        p.error("--runs phải >= 1")
    if args.split == "holdout" and args.limit:
        p.error("Holdout phải chạy đủ, không dùng --limit")
    return args


def _configure_env(mode: str, no_embedding: bool) -> None:
    """Phải gọi TRƯỚC khi import ``src`` vì ``get_settings`` được cache."""
    if mode == "rules":
        os.environ["LLM_DISABLED"] = "true"
        os.environ["EMBEDDING_ENABLED"] = "false"
    elif no_embedding:
        os.environ["EMBEDDING_ENABLED"] = "false"


def _runtime_config(mode: str) -> dict[str, Any]:
    from src.config import get_settings

    s = get_settings()
    cfg: dict[str, Any] = {
        "mode": mode,
        "llm_disabled": s.llm_disabled,
        "embedding_enabled": s.embedding_available,
        "embedding_model": s.embedding_model if s.embedding_available else None,
    }
    if mode == "llm":
        cfg.update(
            provider=s.llm_provider,
            model=s.model_name,
            endpoint_host=urlparse(s.llm_base_url).hostname if s.llm_base_url else "api.openai.com",
            temperature=s.llm_temperature,
            reasoning_effort=s.llm_reasoning_effort,
            planning_model=s.llm_planning_model or None,
            planning_reasoning_effort=s.llm_planning_reasoning_effort,
        )
    return cfg


class ProviderUnavailableError(RuntimeError):
    """Model lỗi hạ tầng (hết quota, timeout, sai key). Pipeline cố ý nuốt lỗi này và hỏi
    lại người dùng (fail-closed), nên nếu cứ chấm thì ca bị tính SAI oan. Thay vào đó dừng
    lượt đo, không ghi ca này, để ``--resume`` chạy lại sau."""


def _run_case(case: protocol.Case, *, run: int, tag: str, model_client: Any, min_interval: float) -> dict[str, Any]:
    from src.agent.pipeline import PipelineDeps
    from src.services.pipeline_bridge import reason

    deps = PipelineDeps()  # mỗi ca một household sạch: không rò ngữ cảnh giữa các ca
    conversation_id = f"eval-{tag}-r{run}-{case.id}"
    now = datetime.fromisoformat(case.now)
    turns_out: list[dict[str, Any]] = []
    error = None
    for idx, turn in enumerate(case.turns):
        if model_client is not None:
            model_client.reset()
        t0 = time.perf_counter()
        try:
            result = reason(
                message=turn.text, conversation_id=conversation_id, user_id="eval-user", role=case.role,
                now=now, deps=deps, live_device_states=case.state or None, model_client=model_client,
            )
        except Exception:  # noqa: BLE001 - ghi lỗi vào kết quả thay vì dừng cả lượt đo
            error = traceback.format_exc(limit=3)
            turns_out.append({"index": idx, "text": turn.text, "error": error,
                              "reasons": ["exception"] if turn.expect is not None else []})
            break
        latency = time.perf_counter() - t0
        calls = list(getattr(model_client, "calls", []) or [])
        if calls and getattr(calls[-1], "provider_error_type", None):
            raise ProviderUnavailableError(calls[-1].provider_error_type)
        actions = [
            {"device_id": a.device_id, "action": a.action.value, "params": dict(a.params or {})}
            for a in (result.candidate_plan.actions if result.candidate_plan else [])
        ]
        reasons = protocol.score_turn(turn.expect, result.outcome, actions) if turn.expect is not None else []
        turns_out.append({
            "index": idx, "text": turn.text, "scored": turn.expect is not None, "outcome": result.outcome,
            "actions": actions, "reply": result.reply, "reasons": reasons, "latency_s": round(latency, 3),
            "llm_calls": len(calls), "tokens": sum(getattr(c, "total_tokens", 0) or 0 for c in calls),
        })
        if calls and min_interval > 0:
            time.sleep(min_interval)
    passed = error is None and all(not t["reasons"] for t in turns_out if t.get("scored", True))
    return {"run": run, "case_id": case.id, "tags": case.tags, "passed": passed, "error": error, "turns": turns_out}


def _write_report(out_dir: Path, manifest: dict[str, Any], summary: dict[str, Any], records: list[dict[str, Any]]) -> None:
    cfg = manifest["config"]
    lines = [
        f"# Kết quả đo: {manifest['dataset']['path']} · {manifest['split']} · {cfg['mode']}",
        "",
        f"- Thời điểm: {manifest['started_at']} → {manifest.get('finished_at', '(chưa xong)')}",
        f"- Commit: `{str(manifest['git'].get('commit'))[:10]}` · nhánh `{manifest['git'].get('branch')}`"
        + (" · **worktree có thay đổi chưa commit**" if manifest["git"].get("dirty") else ""),
        f"- Dataset sha256: `{manifest['dataset']['sha256'][:16]}…` · {manifest['dataset']['n_selected']} ca"
        f" / {manifest['dataset']['n_total']} tổng",
        "- Cấu hình: " + ", ".join(f"{k}={v}" for k, v in cfg.items()),
        "",
        "## Tổng hợp",
        "",
        f"- Độ chính xác trung bình: **{summary['accuracy_mean']:.1%}** (độ lệch {summary['accuracy_std']:.1%},"
        f" min {summary['accuracy_min']:.1%}, max {summary['accuracy_max']:.1%}) qua"
        f" {summary['runs_completed']}/{summary['runs_requested']} lần chạy",
        f"- Luôn đúng {len(summary['always_pass'])} · luôn sai {len(summary['always_fail'])}"
        f" · chập chờn {len(summary['flaky'])} · lỗi ngoại lệ {summary['errors']}",
        f"- Độ trễ mỗi lượt: p50 {summary['latency_p50_s']:.2f}s, p95 {summary['latency_p95_s']:.2f}s",
        f"- Lượt có gọi LLM: {summary['turns_with_llm_pct']:.0%} · trung bình {summary['llm_calls_per_turn']:.2f}"
        f" lần gọi/lượt · {summary['tokens_total']} token",
        "",
        "## Theo tag",
        "",
        "| Tag | Số lượt chấm | Đúng |",
        "|---|---|---|",
        *[f"| {t} | {v['n']} | {v['accuracy']:.0%} |" for t, v in summary["by_tag"].items()],
        "",
        "## Toàn bộ ca sai",
        "",
    ]
    failures = [r for r in records if not r["passed"]]
    if not failures:
        lines.append("Không có.")
    for rec in sorted(failures, key=lambda r: (r["case_id"], r["run"])):
        lines.append(f"### `{rec['case_id']}` · lần {rec['run']}")
        for t in rec["turns"]:
            mark = "✗" if t.get("reasons") else "·"
            lines.append(f"- {mark} “{t['text']}” → {t.get('outcome', 'lỗi')} {t.get('actions', '')}")
            for r in t.get("reasons", []):
                lines.append(f"  - {r}")
        if rec.get("error"):
            lines.append(f"```\n{rec['error'].strip()}\n```")
        lines.append("")
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    resume_manifest: dict[str, Any] | None = None
    if args.resume:
        resume_manifest = json.loads((args.resume / "manifest.json").read_text(encoding="utf-8"))
        args.split, args.mode = resume_manifest["split"], resume_manifest["config"]["mode"]
        args.runs, args.holdout_pct = resume_manifest["runs"], resume_manifest["holdout_pct"]
        args.no_embedding = not resume_manifest["config"].get("embedding_enabled", True) and args.mode == "llm"
    _configure_env(args.mode, args.no_embedding)

    cases_all, dataset_sha = protocol.load_dataset(args.dataset)
    cases = protocol.select_split(cases_all, args.split, args.holdout_pct)
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        print(f"Không có ca nào thuộc split {args.split!r}.", file=sys.stderr)
        return 2

    git = protocol.git_info()
    config = _runtime_config(args.mode)
    if resume_manifest is not None:
        if resume_manifest["dataset"]["sha256"] != dataset_sha:
            print("Dataset đã thay đổi so với lần chạy cũ; không thể chạy tiếp.", file=sys.stderr)
            return 2
        if resume_manifest["config"] != config:
            print("Cấu hình model đã thay đổi so với lần chạy cũ; không thể chạy tiếp.", file=sys.stderr)
            return 2
        out_dir: Path = args.resume
        manifest = resume_manifest
        manifest.setdefault("resumed_at", []).append({"at": datetime.now(UTC).isoformat(timespec="seconds"), "git": git})
    else:
        if args.split == "holdout":
            try:
                protocol.guard_holdout(
                    HOLDOUT_LOG, dataset_path=str(args.dataset), dataset_sha=dataset_sha, git=git,
                    reason=args.reason, allow_rerun=args.rerun_holdout, config=config,
                )
            except protocol.HoldoutGuardError as exc:
                print(f"Từ chối chạy holdout: {exc}", file=sys.stderr)
                return 3
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        out_dir = RESULTS_DIR / f"{stamp}_{args.dataset.stem}_{args.split}_{args.mode}"
        out_dir.mkdir(parents=True, exist_ok=False)
        manifest = {
            "harness_version": protocol.HARNESS_VERSION,
            "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "argv": list(sys.argv[1:] if argv is None else argv),
            "git": git,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "dataset": {"path": str(args.dataset), "sha256": dataset_sha, "n_total": len(cases_all), "n_selected": len(cases)},
            "split": args.split,
            "holdout_pct": args.holdout_pct,
            "split_salt": protocol.SPLIT_SALT,
            "runs": args.runs,
            "min_interval_s": args.min_interval,
            "limit": args.limit or None,
            "config": config,
        }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    records_path = out_dir / "records.jsonl"
    records: list[dict[str, Any]] = []
    if records_path.exists():
        records = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    done = {(r["run"], r["case_id"]) for r in records}

    model_client = None
    if args.mode == "llm":
        from src.nlu.model_client import build_nlu_model_client

        model_client = build_nlu_model_client()
        if model_client is None:
            print("Chế độ llm nhưng chưa cấu hình được model (thiếu key hoặc LLM_DISABLED=true).", file=sys.stderr)
            return 2

    tag = out_dir.name
    total = args.runs * len(cases)
    interrupted: str | None = None
    with records_path.open("a", encoding="utf-8") as fh:
        for run in range(1, args.runs + 1):
            for case in cases:
                if (run, case.id) in done:
                    continue
                try:
                    rec = _run_case(case, run=run, tag=tag, model_client=model_client, min_interval=args.min_interval)
                except ProviderUnavailableError as exc:
                    interrupted = str(exc)
                    break
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
                records.append(rec)
                print(f"[{len(records)}/{total}] lần {run} · {case.id}: {'đúng' if rec['passed'] else 'SAI'}", flush=True)
            if interrupted:
                break

    if interrupted:
        print(
            f"\nModel lỗi hạ tầng ({interrupted}) — dừng, ca đang dở KHÔNG bị chấm. "
            f"Đã xong {len(records)}/{total}. Chạy tiếp khi có quota:\n"
            f"  .venv/bin/python -m evals.run {args.dataset} --resume {out_dir}",
            file=sys.stderr,
        )
        return 4

    manifest["finished_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = protocol.aggregate(records, args.runs)
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_report(out_dir, manifest, summary, records)
    print(f"\nĐộ chính xác {summary['accuracy_mean']:.1%} ± {summary['accuracy_std']:.1%} · báo cáo: {out_dir / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
