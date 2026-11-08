"""Giao thức đo khách quan — phần THUẦN (không gọi pipeline), test được độc lập.

Bốn quy tắc được ép bằng code thay vì nhờ người chạy tự nhớ:

1. Tách tập ``dev`` / ``holdout`` TẤT ĐỊNH theo ``id`` (băm có salt cố định), nên thêm
   ca mới không làm ca cũ đổi phe và không ai "chọn" được ca nào vào holdout.
2. ``holdout`` chỉ chạy MỘT lần cho mỗi phiên bản dataset: mỗi lần chạy được ghi sổ
   (``holdout_log.jsonl``, có commit) TRƯỚC khi chạy; chạy lại phải khai báo rõ và cũng
   bị ghi sổ.
3. Mọi lần chạy kèm manifest: commit, worktree bẩn hay sạch, model, temperature, mức suy
   luận, embedding, dataset hash. Không bao giờ ghi API key.
4. Chạy nhiều lần (mặc định 3) và báo cáo trung bình, độ lệch, min/max, ca chập chờn và
   TOÀN BỘ ca sai — không chỉ con số đẹp.
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HARNESS_VERSION = "1"
SPLIT_SALT = "vinbutler-eval-v1"
DEFAULT_HOLDOUT_PCT = 30
DEFAULT_NOW = "2026-08-20T14:00:00+00:00"


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class Turn:
    text: str
    expect: dict[str, Any] | None = None


@dataclass(slots=True)
class Case:
    id: str
    turns: list[Turn]
    tags: list[str] = field(default_factory=list)
    state: dict[str, dict[str, Any]] = field(default_factory=dict)
    role: str = "owner"
    now: str = DEFAULT_NOW
    split: str | None = None  # None = suy từ id


def parse_case(raw: dict[str, Any]) -> Case:
    """Một dòng JSONL → Case. Dạng rút gọn ``{"text", "expect"}`` = hội thoại một lượt."""
    if "id" not in raw:
        raise ValueError("Ca thiếu trường 'id'")
    if "turns" in raw:
        turns = [Turn(text=t["text"], expect=t.get("expect")) for t in raw["turns"]]
    elif "text" in raw:
        turns = [Turn(text=raw["text"], expect=raw.get("expect"))]
    else:
        raise ValueError(f"Ca {raw['id']!r} thiếu 'text' hoặc 'turns'")
    if not any(t.expect is not None for t in turns):
        raise ValueError(f"Ca {raw['id']!r} không có lượt nào có 'expect'")
    split = raw.get("split")
    if split not in (None, "dev", "holdout"):
        raise ValueError(f"Ca {raw['id']!r} có split không hợp lệ: {split!r}")
    return Case(
        id=str(raw["id"]),
        turns=turns,
        tags=list(raw.get("tags", [])),
        state=dict(raw.get("state", {})),
        role=str(raw.get("role", "owner")),
        now=str(raw.get("now", DEFAULT_NOW)),
        split=split,
    )


def load_dataset(path: Path) -> tuple[list[Case], str]:
    """Đọc JSONL (bỏ dòng trống và dòng ``#``). Trả (cases, sha256 của nội dung file)."""
    data = path.read_bytes()
    cases: list[Case] = []
    seen: set[str] = set()
    for lineno, line in enumerate(data.decode("utf-8").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            case = parse_case(json.loads(line))
        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            raise ValueError(f"{path}:{lineno}: {exc}") from exc
        if case.id in seen:
            raise ValueError(f"{path}:{lineno}: trùng id {case.id!r}")
        seen.add(case.id)
        cases.append(case)
    return cases, hashlib.sha256(data).hexdigest()


def assign_split(case: Case, holdout_pct: int = DEFAULT_HOLDOUT_PCT) -> str:
    """Phe của một ca: ghi tay trong dataset thắng; nếu không thì băm id có salt."""
    if case.split is not None:
        return case.split
    bucket = int(hashlib.sha256(f"{SPLIT_SALT}:{case.id}".encode()).hexdigest(), 16) % 100
    return "holdout" if bucket < holdout_pct else "dev"


def select_split(cases: list[Case], split: str, holdout_pct: int = DEFAULT_HOLDOUT_PCT) -> list[Case]:
    if split == "all":
        return list(cases)
    return [c for c in cases if assign_split(c, holdout_pct) == split]


# ---------------------------------------------------------------------------
# Chấm điểm
# ---------------------------------------------------------------------------
def _value_matches(expected: Any, actual: Any, tol: float) -> bool:
    if isinstance(expected, int | float) and isinstance(actual, int | float) and not isinstance(expected, bool):
        return math.isclose(float(expected), float(actual), abs_tol=tol)
    return bool(expected == actual)


def _action_matches(exp: dict[str, Any], act: dict[str, Any], tol: float) -> bool:
    if exp.get("device_id") != act.get("device_id") or exp.get("action") != act.get("action"):
        return False
    params = act.get("params") or {}
    return all(k in params and _value_matches(v, params[k], tol) for k, v in (exp.get("params") or {}).items())


def score_turn(expect: dict[str, Any], outcome: str, actions: list[dict[str, Any]]) -> list[str]:
    """Trả danh sách lý do SAI (rỗng = đúng).

    ``expect`` hỗ trợ:
      - ``outcome``: chuỗi hoặc danh sách chấp nhận được.
      - ``actions``: danh sách CHÍNH XÁC (không thừa, không thiếu) các
        ``{device_id, action, params?}``; ``params`` so khớp tập con.
      - ``actions_include``: chỉ cần có mặt, cho phép thừa.
      - ``tol``: dung sai số (mặc định 0).
    """
    reasons: list[str] = []
    tol = float(expect.get("tol", 0))
    want = expect.get("outcome")
    if want is not None:
        allowed = want if isinstance(want, list) else [want]
        if outcome not in allowed:
            reasons.append(f"outcome={outcome!r}, cần {allowed}")
    for key, exact in (("actions", True), ("actions_include", False)):
        if key not in expect:
            continue
        remaining = list(actions)
        for exp in expect[key]:
            hit = next((a for a in remaining if _action_matches(exp, a, tol)), None)
            if hit is None:
                reasons.append(f"thiếu {exp}")
            else:
                remaining.remove(hit)
        if exact and remaining:
            reasons.append(f"thừa {remaining}")
    return reasons


# ---------------------------------------------------------------------------
# Manifest và sổ holdout
# ---------------------------------------------------------------------------
def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def git_info() -> dict[str, Any]:
    status = _git("status", "--porcelain")
    info: dict[str, Any] = {
        "commit": _git("rev-parse", "HEAD"),
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(status),
    }
    if status:
        # Băm diff để hai lần chạy trên cùng worktree bẩn vẫn so được với nhau.
        info["diff_sha256"] = hashlib.sha256(_git("diff", "HEAD").encode()).hexdigest()
    return info


class HoldoutGuardError(RuntimeError):
    pass


def holdout_entries(log_path: Path, dataset_sha: str) -> list[dict[str, Any]]:
    if not log_path.exists():
        return []
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if r.get("dataset_sha256") == dataset_sha]


def guard_holdout(
    log_path: Path,
    *,
    dataset_path: str,
    dataset_sha: str,
    git: dict[str, Any],
    reason: str,
    allow_rerun: bool,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Kiểm tra điều kiện chạy holdout rồi GHI SỔ trước khi chạy (lần chạy bị huỷ giữa
    chừng vẫn tính là đã nhìn holdout). Trả bản ghi đã ghi."""
    if git.get("dirty"):
        raise HoldoutGuardError("Holdout chỉ chạy trên worktree sạch: commit thay đổi trước để kết quả gắn với một commit.")
    if not reason.strip():
        raise HoldoutGuardError("Holdout cần --reason mô tả vì sao chạy (ví dụ: 'số liệu cuối cho báo cáo giữa kỳ').")
    previous = holdout_entries(log_path, dataset_sha)
    if previous and not allow_rerun:
        last = previous[-1]
        raise HoldoutGuardError(
            f"Dataset này đã chạy holdout {len(previous)} lần (gần nhất {last.get('at')} ở commit "
            f"{str(last.get('commit'))[:7]}). Chạy lại phải thêm --rerun-holdout và sẽ được ghi sổ."
        )
    entry = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "dataset": dataset_path,
        "dataset_sha256": dataset_sha,
        "commit": git.get("commit"),
        "reason": reason.strip(),
        "rerun": bool(previous),
        "previous_runs": len(previous),
        "config": config,
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


# ---------------------------------------------------------------------------
# Tổng hợp
# ---------------------------------------------------------------------------
def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct / 100
    lo, hi = math.floor(k), math.ceil(k)
    return ordered[lo] if lo == hi else ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def aggregate(records: list[dict[str, Any]], runs: int) -> dict[str, Any]:
    """``records``: một dòng cho mỗi (run, case) với ``passed``, ``tags``, ``turns``."""
    per_run: dict[int, list[bool]] = {}
    per_case: dict[str, list[bool]] = {}
    per_tag: dict[str, list[bool]] = {}
    latencies: list[float] = []
    llm_calls: list[int] = []
    tokens = 0
    errors = 0
    for rec in records:
        ok = bool(rec["passed"])
        per_run.setdefault(rec["run"], []).append(ok)
        per_case.setdefault(rec["case_id"], []).append(ok)
        for tag in rec.get("tags") or ["(không gắn tag)"]:
            per_tag.setdefault(tag, []).append(ok)
        errors += 1 if rec.get("error") else 0
        for turn in rec.get("turns", []):
            latencies.append(float(turn.get("latency_s", 0.0)))
            llm_calls.append(int(turn.get("llm_calls", 0)))
            tokens += int(turn.get("tokens", 0))
    run_acc = [sum(v) / len(v) for _, v in sorted(per_run.items()) if v]
    return {
        "runs_completed": len(per_run),
        "runs_requested": runs,
        "cases": len(per_case),
        "accuracy_per_run": run_acc,
        "accuracy_mean": statistics.fmean(run_acc) if run_acc else 0.0,
        "accuracy_std": statistics.stdev(run_acc) if len(run_acc) > 1 else 0.0,
        "accuracy_min": min(run_acc, default=0.0),
        "accuracy_max": max(run_acc, default=0.0),
        "always_pass": sorted(c for c, v in per_case.items() if all(v)),
        "always_fail": sorted(c for c, v in per_case.items() if not any(v)),
        "flaky": sorted(c for c, v in per_case.items() if any(v) and not all(v)),
        "by_tag": {t: {"n": len(v), "accuracy": sum(v) / len(v)} for t, v in sorted(per_tag.items())},
        "latency_p50_s": _percentile(latencies, 50),
        "latency_p95_s": _percentile(latencies, 95),
        "llm_calls_per_turn": statistics.fmean(llm_calls) if llm_calls else 0.0,
        "turns_with_llm_pct": (sum(1 for c in llm_calls if c) / len(llm_calls)) if llm_calls else 0.0,
        "tokens_total": tokens,
        "errors": errors,
    }
