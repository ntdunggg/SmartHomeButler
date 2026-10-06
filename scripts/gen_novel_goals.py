"""Sinh bộ NOVEL-GOAL mở rộng để đo GENERALIZATION (concern #3).

Mục tiêu: hàng chục case CHƯA từng xuất hiện trong prompt/code — paraphrase lạ, mục
tiêu môi trường gián tiếp, multi-device, context-dependent, negation, correction,
underspecified. Tất cả route=llm; nhãn TỐI GIẢN (`novel` + `category`) vì không có tập
đóng để chấm intent — `novel_goal_success_rate` chấm theo KẾT QUẢ (kế hoạch grounded,
đúng capability, không ảo giác) HOẶC clarify sạch.

Quy ước an toàn để KHÔNG bị fast-path tất định:
- KHÔNG ghép "động từ điều khiển (bật/tắt/đặt…) + tên thiết bị đã khớp" trong một câu —
  cặp đó bị `understand()` bắt thành DEVICE_COMMAND và chạy offline, không tới LLM.
- focus_room chỉ lấy từ registry ROOMS (validate — sai phòng thì fail sớm, không âm thầm).

Đây là bộ STAGING để NGƯỜI duyệt trước khi promote:
    python -m scripts.gen_novel_goals            # ghi staging + in tóm tắt theo category
    python -m scripts.gen_novel_goals --promote  # copy đè src/evaluation/datasets/novel_goals.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.agent.text import strip_diacritics
from src.iot.registry import ROOMS

STAGING = Path("src/evaluation/datasets_staging/novel_goals_expanded.jsonl")
PROMOTE_TO = Path("src/evaluation/datasets/novel_goals.jsonl")

LR = "Phòng khách"
BR_PARENT = "Phòng ngủ bố mẹ"
BR_KID = "Phòng ngủ con"
KITCHEN = "Phòng bếp"


def nod(s: str) -> str:
    return strip_diacritics(s)


def rec(id_: str, utt: str, category: str, *, room: str | None = None,
        ctx_extra: dict | None = None, decision: str | None = None) -> dict:
    if room is not None and room not in ROOMS:
        raise ValueError(f"focus_room không có thật: {room!r} (ROOMS={sorted(ROOMS)})")
    ctx: dict = {}
    if room is not None:
        ctx["focus_room"] = room
    if ctx_extra:
        ctx.update(ctx_extra)
    expect: dict = {"novel": True, "category": category}
    if decision is not None:
        expect["decision"] = decision
    return {"id": id_, "utterance": utt, "context": ctx, "expect": expect, "expected_route": "llm"}


# 1. Paraphrase lạ của routine — không keyword ("đi ngủ"/"xem phim"…) trực tiếp.
PARAPHRASE = [
    ("mình cần một không gian thật yên tĩnh để đọc sách", BR_PARENT),
    ("tạo không khí ấm cúng lãng mạn cho bữa tối hai người", LR),
    ("sắp có cuộc họp video quan trọng, sửa soạn chỗ ngồi giúp tôi", BR_PARENT),
    ("tôi muốn ngồi thiền khoảng mười lăm phút", LR),
    ("cả nhà đi chơi xa cuối tuần, lo liệu cho an toàn và tiết kiệm điện", LR),
    ("tôi vừa tập thể dục xong, người nóng ran", BR_PARENT),
    ("khách sắp tới, muốn phòng thật chỉn chu gọn gàng", LR),
    ("bé nhà mình sắp vào giấc, nhẹ nhàng giúp con", BR_KID),
    ("tối nay muốn tự thưởng một buổi tối thư thái", LR),
    ("cần sự tỉnh táo để làm cho xong deadline đêm nay", BR_PARENT),
]

# 2. Mục tiêu MÔI TRƯỜNG gián tiếp — nêu triệu chứng, không nói thiết bị/hành động.
INDIRECT_ENV = [
    ("mắt tôi mỏi nhừ vì nhìn màn hình cả ngày", BR_PARENT),
    ("trong phòng có mùi ẩm mốc khó chịu", BR_KID),
    ("hôm nay tôi bị dị ứng phấn hoa, hắt hơi liên tục", LR),
    ("nắng chiều hắt thẳng vào mặt chói quá", LR),
    ("da tôi khô rát vì ngồi lâu chỗ này", BR_PARENT),
    ("cảm giác thiếu oxy, hơi choáng váng", BR_KID),
    ("người tôi ướt đẫm, bức bối khó chịu", LR),
    ("phòng cứ tối tối âm u làm tôi buồn ngủ giữa ban ngày", KITCHEN),
    ("tiếng tivi cứ oang oang, đầu tôi nhức", LR),
    ("chăn mỏng mà đêm nay trở lạnh", BR_KID),
]

# 3. Multi-device — một mục tiêu cần phối HỢP nhiều thiết bị.
MULTI_DEVICE = [
    ("biến phòng khách thành rạp chiếu phim tại gia đi", LR),
    ("dựng không khí tiệc tùng sôi động cho buổi họp mặt", LR),
    ("sắp xếp phòng con thật lý tưởng cho giờ đi ngủ của bé", BR_KID),
    ("chuẩn bị phòng ngủ chu đáo trước khi tôi vào giường", BR_PARENT),
    ("thiết lập góc làm việc buổi tối vừa sáng vừa mát", BR_PARENT),
    ("làm cho cả phòng khách vừa dịu sáng vừa trong lành", LR),
    ("chuyển phòng bếp sang chế độ nấu nướng gọn gàng thoáng đãng", KITCHEN),
]

# 4. Context-dependent — CÙNG câu, khác context → kỳ vọng plan khác (đối chứng).
CONTEXT_PAIRS = [
    ("làm cho ở đây dễ chịu hơn một chút", LR, None),
    ("làm cho ở đây dễ chịu hơn một chút", BR_KID, None),
    ("chuẩn bị mọi thứ để nghỉ ngơi", BR_PARENT, None),
    ("chuẩn bị mọi thứ để nghỉ ngơi", BR_KID, None),
    ("cái vừa nãy làm phiền quá, bạn lo giúp tôi nhé", LR, {"last_device_id": "tv_phong_khach"}),
    ("cái vừa nãy làm phiền quá, bạn lo giúp tôi nhé", LR, {"last_device_id": "dieu_hoa_phong_khach"}),
]

# 5. Negation của mục tiêu tự do (không phải lệnh thiết bị tường minh).
NEGATION = [
    ("tối nay đừng để phòng lạnh như hôm qua nhé", BR_PARENT),
    ("tôi không muốn có tiếng ồn nào lúc này", LR),
    ("đừng làm gì khiến phòng chói mắt", BR_KID),
    ("muốn hoàn toàn yên tĩnh, không một tiếng ồn nào", BR_PARENT),
    ("làm mát phòng nhưng đừng để khô da", LR),
    ("cần rõ mặt hơn một chút nhưng tuyệt đối không chói mắt", BR_KID),
]

# 6. Correction giữa câu trên mục tiêu tự do.
CORRECTION = [
    ("cho phòng ấm áp hơn, à không, mát mẻ mới đúng", LR),
    ("chuẩn bị xem phim, mà thôi, để tôi tập trung làm việc", BR_PARENT),
    ("làm cho thật rực rỡ, ý tôi là dịu nhẹ thôi", BR_KID),
    ("tạo không khí thật náo nhiệt, nhầm, yên tĩnh mới phải", LR),
    ("chuẩn bị cho bé thức dậy, à quên, cho bé đi ngủ", BR_KID),
]

# 7. Underspecified novel — quá mơ hồ, KHÔNG có focus_room → phải clarify sạch.
UNDERSPECIFIED = [
    "làm gì đó đi",
    "giúp tôi với",
    "chỉnh lại cho hợp lý hơn",
    "tôi cần thay đổi không khí một chút",
    "cho nó ổn hơn đi",
    "xử lý giúp tôi cái này",
]


def build() -> list[dict]:
    rows: list[dict] = []
    i = 0
    for utt, room in PARAPHRASE:
        i += 1
        rows.append(rec(f"nvx-par-{i:03d}", utt, "paraphrase_unusual", room=room))
    for utt, room in INDIRECT_ENV:
        i += 1
        rows.append(rec(f"nvx-env-{i:03d}", utt, "indirect_environment", room=room))
        # thêm biến thể không dấu cho một phần (robust hoá đầu vào)
        if i % 3 == 0:
            i += 1
            rows.append(rec(f"nvx-env-{i:03d}", nod(utt), "indirect_environment", room=room))
    for utt, room in MULTI_DEVICE:
        i += 1
        rows.append(rec(f"nvx-mul-{i:03d}", utt, "multi_device", room=room))
    for utt, room, extra in CONTEXT_PAIRS:
        i += 1
        rows.append(rec(f"nvx-ctx-{i:03d}", utt, "context_dependent", room=room, ctx_extra=extra))
    for utt, room in NEGATION:
        i += 1
        rows.append(rec(f"nvx-neg-{i:03d}", utt, "negation", room=room))
    for utt, room in CORRECTION:
        i += 1
        rows.append(rec(f"nvx-cor-{i:03d}", utt, "correction", room=room))
    for utt in UNDERSPECIFIED:
        i += 1
        # KHÔNG focus_room → mục tiêu không đủ cụ thể → clarify là đúng.
        rows.append(rec(f"nvx-und-{i:03d}", utt, "underspecified", decision="clarify"))
    _assert_all_llm_routed(rows)
    return rows


def _assert_all_llm_routed(rows: list[dict]) -> None:
    """Bảo đảm KHÔNG case nào bị luật tất định bắt thành DEVICE_COMMAND (fast-path).

    Nếu bị bắt, câu đó chạy offline không tới LLM → không đo được generalization. Fold dấu
    hay gây va chạm token (tất→tat=tắt, động→dong=đóng, lên=on) — guard này chặn sớm."""
    from datetime import UTC, datetime

    from src.nlu.context import build_runtime_context
    from src.nlu.normalizer import analyze
    from src.nlu.understanding import understand

    now = datetime(2026, 8, 5, 22, 0, tzinfo=UTC)
    caught = []
    for r in rows:
        room = r["context"].get("focus_room")
        nu = analyze(r["utterance"], focus_room=room)
        ctx = build_runtime_context(nu, now=now, focus_room=room)
        u = understand(nu, ctx)
        if u.goal is not None and u.goal.action_hint is not None:
            caught.append((r["id"], r["utterance"]))
    if caught:
        lines = "\n".join(f"  {cid}: {utt!r}" for cid, utt in caught)
        raise SystemExit(
            f"{len(caught)} case bị fast-path thành DEVICE_COMMAND (sửa lời cho tránh động từ "
            f"điều khiển / token va chạm khi fold dấu):\n{lines}"
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--promote", action="store_true", help="Ghi đè datasets/novel_goals.jsonl (sau khi đã duyệt).")
    args = ap.parse_args()

    rows = build()
    from collections import Counter
    cats = Counter(r["expect"]["category"] for r in rows)

    out = PROMOTE_TO if args.promote else STAGING
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")

    print(f"Đã ghi {len(rows)} novel case → {out}")
    for cat, n in sorted(cats.items()):
        print(f"  {cat:22} {n}")
    if not args.promote:
        print("\nDuyệt file trên rồi chạy lại với --promote để đưa vào datasets/novel_goals.jsonl")


if __name__ == "__main__":
    main()
