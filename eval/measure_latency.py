"""Đo độ trễ đầu-cuối của agent để chứng minh ràng buộc < 2s của đề bài.

Chạy: ``python -m eval.measure_latency``

Đo qua đúng đường mà frontend đi (HTTP → FastAPI → LangGraph → IoT bus), không
phải đo riêng NLU, nên con số phản ánh trải nghiệm thật của người dùng.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from httpx import ASGITransport, AsyncClient  # noqa: E402

# Bộ câu lệnh phủ các nhóm: đơn lẻ, có tham số, kịch bản, không dấu, mơ hồ, truy vấn
COMMANDS = [
    "bật đèn phòng khách",
    "tắt đèn phòng khách",
    "điều hoà phòng ngủ 24 độ",
    "đặt độ sáng đèn bếp 60%",
    "bat den phong khach",
    "khoá cửa",
    "đóng cửa sổ",
    "kéo rèm",
    "quạt mức 2",
    "âm lượng tv 35",
    "tối nay có khách, chuẩn bị phòng khách",
    "đi ngủ thôi",
    "dọn nhà nào",
    "tắt đèn",
    "đèn phòng khách đang thế nào",
]

ROUNDS = 5
BUDGET_MS = 2000
RESULT_PATH = Path(__file__).resolve().parent / "results" / "latency_by_difficulty.json"


def classify_command(command: str) -> str:
    """Gán nhóm độ khó theo đặc điểm xử lý của câu lệnh trong bộ đo."""
    if command in {"tối nay có khách, chuẩn bị phòng khách", "đi ngủ thôi", "dọn nhà nào"}:
        return "đa bước"
    if any(token in command for token in ("độ", "%", "mức", "âm lượng")):
        return "có tham số"
    if command in {"tắt đèn", "đèn phòng khách đang thế nào"}:
        return "mơ hồ / truy vấn trạng thái"
    return "đơn giản"


async def main() -> int:
    from src.db.seed import run_seed
    from src.main import app

    run_seed()

    latencies: list[tuple[str, int, str]] = []

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://eval") as client:
        login = await client.post("/api/v1/auth/login", json={"username": "bo", "password": "demo1234"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        for _ in range(ROUNDS):
            for command in COMMANDS:
                response = await client.post(
                    "/api/v1/agent/command", headers=headers, json={"message": command}
                )
                body = response.json()
                latencies.append((command, body["latency_ms"], body["nlu_source"]))

    return report(latencies)


def report(latencies: list[tuple[str, int, str]]) -> int:
    values = sorted(ms for _, ms, _ in latencies)
    count = len(values)

    def percentile(p: float) -> int:
        return values[min(count - 1, int(count * p))]

    rules_count = sum(1 for _, _, source in latencies if source == "rules")

    print(f"Số lệnh đo: {count} ({len(COMMANDS)} câu × {ROUNDS} vòng)")
    print(f"p50: {percentile(0.50)}ms")
    print(f"p95: {percentile(0.95)}ms")
    print(f"p99: {percentile(0.99)}ms")
    print(f"max: {values[-1]}ms")
    print(f"trung bình: {statistics.mean(values):.1f}ms")
    print(f"Xử lý bằng NLU rule-based (không gọi LLM): {rules_count}/{count} = {rules_count / count:.0%}")

    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for command, latency_ms, source in latencies:
        grouped[classify_command(command)].append(
            {"command": command, "latency_ms": latency_ms, "nlu_source": source}
        )

    difficulty_order = ["đa bước", "có tham số", "mơ hồ / truy vấn trạng thái", "đơn giản"]
    grouped_summary: dict[str, dict[str, object]] = {}
    print("\nThời gian trung bình theo độ khó (khó → dễ):")
    for group in difficulty_order:
        rows = grouped[group]
        values = [float(row["latency_ms"]) for row in rows]
        summary = {
            "n_samples": len(values),
            "mean_ms": round(statistics.mean(values), 1),
            "p50_ms": round(statistics.median(values), 1),
            "max_ms": round(max(values), 1),
            "samples": rows,
        }
        grouped_summary[group] = summary
        print(
            f"  {group:30s}: {summary['mean_ms']:.1f}ms "
            f"(n={summary['n_samples']}, p50={summary['p50_ms']:.1f}ms, max={summary['max_ms']:.1f}ms)"
        )

    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "n_samples": count,
                "rounds": ROUNDS,
                "commands_per_round": len(COMMANDS),
                "difficulty_order": difficulty_order,
                "groups": grouped_summary,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Đã lưu phân rã latency → {RESULT_PATH}")

    over_budget = [(cmd, ms) for cmd, ms, _ in latencies if ms >= BUDGET_MS]
    if over_budget:
        print(f"\n❌ {len(over_budget)} lệnh vượt ngưỡng {BUDGET_MS}ms:")
        for cmd, ms in over_budget[:10]:
            print(f"   {ms}ms — {cmd}")
        return 1

    print(f"\n✅ Toàn bộ {count} lệnh đều dưới ngưỡng {BUDGET_MS}ms của đề bài.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
