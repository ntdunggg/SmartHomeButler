"""Goldenset 100 câu: CÂU MƠ HỒ + YÊU CẦU MƠ HỒ + HỘI THOẠI DÀI.

Mục tiêu: đo khả năng agent (1) hỏi lại đúng khi thiếu thông tin, (2) suy luận yêu cầu mơ hồ
thành kế hoạch grounded, (3) giữ ngữ cảnh qua nhiều lượt (anaphora, refine, carry, own-room,
huỷ, slot-fill).

Mỗi CASE là một kịch bản gồm 1+ lượt; mỗi lượt nêu `expect` = tập outcome chấp nhận được
(candidate_plan | clarification | answer | cancelled | no_goal), tuỳ chọn `dev_any` (ít nhất
một slug con này xuất hiện trong plan) và `dev_none` (không slug nào trong này).

Chạy:  python -m src.evaluation.goldenset_ambiguity            # engine fake (offline, mặc định)
       python -m src.evaluation.goldenset_ambiguity --real     # MODEL_NAME thật
       python -m src.evaluation.goldenset_ambiguity --dump out.jsonl   # xuất goldenset ra JSONL
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.agent.pipeline import PipelineDeps
from src.services.pipeline_bridge import reason

_NOW = datetime(2026, 8, 12, 22, 0, tzinfo=UTC)
LR, KIT, PAR, KID = "Phòng khách", "Phòng bếp", "Phòng ngủ bố mẹ", "Phòng ngủ con"


@dataclass
class Turn:
    utt: str
    expect: set[str]
    ctx: dict[str, Any] = field(default_factory=dict)
    dev_any: tuple[str, ...] = ()
    dev_none: tuple[str, ...] = ()


@dataclass
class Case:
    id: str
    category: str
    turns: list[Turn]


def _t(utt: str, expect, **kw) -> Turn:
    exp = {expect} if isinstance(expect, str) else set(expect)
    return Turn(utt=utt, expect=exp, ctx=kw.get("ctx", {}), dev_any=kw.get("dev_any", ()), dev_none=kw.get("dev_none", ()))


# ---------------------------------------------------------------------------
# A. CÂU MƠ HỒ — thiếu thiết bị/phòng, tham chiếu treo, quá chung → phải HỎI LẠI
# ---------------------------------------------------------------------------
_A = [
    ("tắt đèn", "clarification"),
    ("bật đèn lên", "clarification"),
    ("mở ra", "clarification"),
    ("đóng lại", "clarification"),
    ("bật lên đi", "clarification"),
    ("tắt đi", "clarification"),
    ("chỉnh nhiệt độ", "clarification"),
    ("tăng lên", "clarification"),
    ("giảm xuống chút", "clarification"),
    ("để nó to hơn", "clarification"),
    ("nhỏ lại giúp tôi", "clarification"),
    ("tắt nó đi", "clarification"),          # không tiền lệ trong phiên
    ("bật cái đó lên", "clarification"),
    ("mở cái kia ra", "clarification"),
    ("làm gì đó đi", "clarification"),
    ("giúp tôi với", "clarification"),
    ("xử lý giúp tôi cái này", "clarification"),
    ("chỉnh lại cho hợp lý hơn", "clarification"),
    ("cho nó ổn hơn đi", "clarification"),
    ("làm cái gì đó cho phòng", "clarification"),
    ("phòng ngủ", "clarification"),          # bare, 2 phòng ngủ
    ("đèn", "clarification"),
    ("điều hoà", "clarification"),
    ("cái quạt", "clarification"),
    ("bật", "clarification"),
    ("tắt hết", "clarification"),            # 'hết' không kèm loại
    ("mở hết ra", "clarification"),
    ("tăng nhiệt độ lên tí", "clarification"),
    ("giảm độ sáng", "clarification"),       # không thiết bị/tiền lệ
    ("để yên tĩnh hơn", "clarification"),
    ("bật đèn phòng", "clarification"),      # 'phòng' cụt
    ("tắt đèn ở đó", "clarification"),
    ("mở rèm", "clarification"),             # nhiều rèm
    ("đóng cửa sổ", "clarification"),        # nhiều cửa sổ
    ("bật tivi", "clarification"),           # nhiều tv
    ("tắt loa", {"candidate_plan", "clarification"}),   # chỉ 1 loa → resolve được là hợp lệ
    ("chỉnh cho sáng hơn", "clarification"),
    ("cho ấm hơn đi", "clarification"),
    ("tối quá", "clarification"),            # không phòng
    ("ồn quá", "clarification"),             # không phòng
    ("mát lên đi", "clarification"),
    ("ấm lên chút", "clarification"),
    ("sáng lên nào", "clarification"),
    ("to lên tí", "clarification"),
    ("khoá lại", "clarification"),           # khoá nào
    ("mở khoá đi", "clarification"),
    ("bật máy lọc", "clarification"),        # nhiều máy lọc
    ("tắt bớt đi", "clarification"),
]

# ---------------------------------------------------------------------------
# B. YÊU CẦU MƠ HỒ (open-ended) — nêu triệu chứng/mục tiêu + PHÒNG → suy thành kế hoạch
#    (một số quá mơ hồ vẫn chấp nhận clarify)
# ---------------------------------------------------------------------------
_B = [
    ("nóng quá đi", {"candidate_plan"}, LR),
    ("oi bức không chịu được", {"candidate_plan"}, LR),
    ("trong phòng chói mắt quá", {"candidate_plan"}, KID),
    ("tối quá chẳng thấy gì", {"candidate_plan"}, KIT),
    ("ồn quá đầu óc không tập trung được", {"candidate_plan"}, LR),
    ("phòng bí quá khó thở", {"candidate_plan"}, PAR),
    ("lạnh run hết cả người", {"candidate_plan"}, PAR),
    ("nắng chiều hắt vào chói quá", {"candidate_plan"}, LR),
    ("chuẩn bị đi ngủ đi", {"candidate_plan"}, KID),
    ("chuẩn bị xem phim nào", {"candidate_plan"}, LR),
    ("tôi muốn ngồi đọc sách thư giãn", {"candidate_plan", "clarification"}, PAR),
    ("làm cho phòng dễ chịu hơn chút", {"candidate_plan", "clarification"}, LR),
    ("tạo không khí ấm cúng cho bữa tối", {"candidate_plan", "clarification"}, LR),
    ("người nóng ran vừa tập thể dục xong", {"candidate_plan"}, PAR),
    ("mắt mỏi vì nhìn màn hình cả ngày", {"candidate_plan", "clarification"}, PAR),
    ("phòng có mùi ẩm mốc khó chịu", {"candidate_plan", "clarification"}, KID),
    ("muốn không gian yên tĩnh để ngủ trưa", {"candidate_plan", "clarification"}, KID),
    ("chuẩn bị chỗ làm việc buổi tối", {"candidate_plan", "clarification"}, PAR),
    ("cho phòng sáng sủa lên tí", {"candidate_plan"}, KIT),
    ("làm mát phòng giúp tôi", {"candidate_plan"}, LR),
    ("giảm bớt ánh sáng cho dịu mắt", {"candidate_plan"}, KID),
    ("bật gì đó cho đỡ ngột ngạt", {"candidate_plan", "clarification"}, PAR),
    ("tôi cần tỉnh táo làm việc khuya", {"candidate_plan", "clarification"}, PAR),
    ("cho không khí trong lành hơn", {"candidate_plan", "clarification"}, LR),
    ("phòng nóng và bí, xử lý giúp", {"candidate_plan"}, LR),
]

# ---------------------------------------------------------------------------
# C. HỘI THOẠI DÀI — nhiều lượt, giữ ngữ cảnh
# ---------------------------------------------------------------------------
def _conversations() -> list[Case]:
    cases: list[Case] = []

    cases.append(Case("C01-anaphora", "hoi_thoai_dai", [
        _t("bật đèn phòng khách", "candidate_plan", dev_any=("den_chum_phong_khach",)),
        _t("tắt nó đi", "candidate_plan", dev_any=("den_chum_phong_khach",)),
    ]))
    cases.append(Case("C02-refine-room", "hoi_thoai_dai", [
        _t("trong phòng chói mắt quá", "candidate_plan", ctx={"speaker_location": LR}),
        _t("à ở phòng ngủ con mà", "candidate_plan", dev_any=("den_ngu_con",), dev_none=("den_chum_phong_khach",)),
    ]))
    cases.append(Case("C03-carry-adjust", "hoi_thoai_dai", [
        _t("trong phòng chói mắt quá", "candidate_plan", ctx={"speaker_location": KID}),
        _t("giảm bớt độ sáng nữa đi", "candidate_plan", dev_any=("den_ngu_con",)),
    ]))
    cases.append(Case("C04-slotfill", "hoi_thoai_dai", [
        _t("tắt đèn", "clarification"),
        _t("phòng khách", "candidate_plan", dev_any=("den_chum_phong_khach",)),
    ]))
    cases.append(Case("C05-cancel-pending", "hoi_thoai_dai", [
        _t("tắt đèn", "clarification"),
        _t("thôi khỏi", {"cancelled", "answer", "no_goal"}),
    ]))
    cases.append(Case("C06-ownroom-parent", "hoi_thoai_dai", [
        _t("bật đèn phòng ngủ của tôi", "candidate_plan",
           ctx={"speaker_home_room": PAR}, dev_any=("den_ngu_bo_me",), dev_none=("den_ngu_con",)),
        _t("tắt bớt đèn bàn làm việc đi", {"candidate_plan", "clarification"}),
    ]))
    cases.append(Case("C07-ownroom-kid", "hoi_thoai_dai", [
        _t("đèn phòng của con sáng quá", {"candidate_plan", "clarification"},
           ctx={"speaker_home_room": KID}),
        _t("giảm xuống nữa đi", {"candidate_plan", "clarification"}),
    ]))
    cases.append(Case("C08-type-in-room", "hoi_thoai_dai", [
        _t("tắt đèn phòng ngủ con", "candidate_plan", dev_any=("den_ngu_con", "den_ban_hoc")),
        _t("bật lại đi", {"candidate_plan", "clarification"}),
    ]))
    cases.append(Case("C09-state-query", "hoi_thoai_dai", [
        _t("điều hoà phòng khách đang bật không", {"answer", "clarification"}),
        _t("thế còn đèn thì sao", {"answer", "clarification"}),
    ]))
    cases.append(Case("C10-negation", "hoi_thoai_dai", [
        _t("đừng bật đèn phòng khách nhé", {"answer", "no_goal", "candidate_plan"}),
        _t("bật điều hoà phòng khách lên", "candidate_plan", dev_any=("dieu_hoa_phong_khach",)),
    ]))
    cases.append(Case("C11-two-pending-global-cancel", "hoi_thoai_dai", [
        _t("tắt đèn", "clarification"),
        _t("chỉnh nhiệt độ", "clarification"),
        _t("huỷ hết đi", {"cancelled", "answer"}),
    ]))
    cases.append(Case("C12-correction-comma", "hoi_thoai_dai", [
        _t("bật đèn phòng bếp, à nhầm, phòng khách", {"candidate_plan", "clarification"},
           dev_none=("den_bep",)),
    ]))
    cases.append(Case("C13-multi-turn-mixed", "hoi_thoai_dai", [
        _t("bật điều hoà phòng ngủ bố mẹ", "candidate_plan", dev_any=("dieu_hoa_phong_bo_me",)),
        _t("nóng quá giảm nhiệt độ đi", {"candidate_plan", "clarification"}),
        _t("thôi đủ rồi cảm ơn", {"answer", "no_goal", "cancelled", "clarification"}),
    ]))
    return cases


def build_cases() -> list[Case]:
    cases: list[Case] = []
    for i, (utt, exp) in enumerate(_A, 1):
        cases.append(Case(f"A{i:02d}", "cau_mo_ho", [_t(utt, exp)]))
    for i, (utt, exp, room) in enumerate(_B, 1):
        cases.append(Case(f"B{i:02d}", "yeu_cau_mo_ho", [_t(utt, exp, ctx={"focus_room": room})]))
    cases.extend(_conversations())
    return cases


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
def _plan_devices(outcome) -> list[str]:
    return [a.device_id for a in getattr(outcome.candidate_plan, "actions", []) or []]


def run(cases: list[Case], *, real: bool) -> None:
    model: Any
    if real:
        from src.nlu.model_client import build_nlu_model_client
        model = build_nlu_model_client()
        engine = f"{model.provider}/{model.model} (thật)" if model is not None else "model không khả dụng"
    else:
        from src.core.reasoning import FakeReasoningModel
        model = FakeReasoningModel()
        engine = "FakeReasoningModel (offline)"

    total_turns = sum(len(c.turns) for c in cases)
    print(f"Engine: {engine} | {len(cases)} case, {total_turns} lượt\n")

    by_cat: dict[str, list[int]] = {}
    fails: list[str] = []
    for c in cases:
        deps = PipelineDeps(model_client=model)
        sid = f"gs-{c.id}"
        ok_case = True
        for j, turn in enumerate(c.turns, 1):
            o = reason(
                message=turn.utt,
                conversation_id=sid,
                now=_NOW,
                model_client=model,
                deps=deps,
                **turn.ctx,
            )
            devs = _plan_devices(o)
            ok = o.outcome in turn.expect
            if ok and turn.dev_any:
                ok = any(d in devs for d in turn.dev_any)
            if ok and turn.dev_none:
                ok = not any(d in devs for d in turn.dev_none)
            if not ok:
                ok_case = False
                fails.append(f"  [{c.id} L{j}] {turn.utt!r} → got={o.outcome} devs={devs} | expect={sorted(turn.expect)}"
                             + (f" any={turn.dev_any}" if turn.dev_any else "")
                             + (f" none={turn.dev_none}" if turn.dev_none else ""))
        by_cat.setdefault(c.category, []).append(1 if ok_case else 0)

    print("=== KẾT QUẢ THEO NHÓM (case pass / tổng) ===")
    grand_ok = grand_n = 0
    for cat, res in by_cat.items():
        p, n = sum(res), len(res)
        grand_ok += p
        grand_n += n
        print(f"  {cat:16} {p:3}/{n:<3}  ({100*p/n:.0f}%)")
    print(f"  {'TỔNG':16} {grand_ok:3}/{grand_n:<3}  ({100*grand_ok/grand_n:.0f}%)")

    if fails:
        print(f"\n=== {len(fails)} LƯỢT KHÔNG ĐẠT ===")
        for f in fails:
            print(f)


def dump_jsonl(cases: list[Case], path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for c in cases:
            row = {"id": c.id, "category": c.category, "turns": [
                {"utterance": t.utt, "ctx": t.ctx, "expect": sorted(t.expect),
                 "dev_any": list(t.dev_any), "dev_none": list(t.dev_none)} for t in c.turns]}
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"Đã ghi {len(cases)} case → {path}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true", help="Chạy với model thật từ MODEL_NAME")
    ap.add_argument("--dump", metavar="PATH", help="Xuất goldenset ra JSONL rồi thoát")
    args = ap.parse_args()
    cases = build_cases()
    if args.dump:
        dump_jsonl(cases, args.dump)
        return
    run(cases, real=args.real)


if __name__ == "__main__":
    main()
