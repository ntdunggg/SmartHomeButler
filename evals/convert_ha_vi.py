"""Chuyển test tiếng Việt của Home Assistant (OHF-Voice/intents, ``tests/vi``) thành dataset
cho ``evals/run.py``.

Nhà mẫu của HA khác nhà demo, nên tên thiết bị và khu vực được ánh xạ theo
``evals/datasets/ha_vi_mapping.yaml``. Ca không ánh xạ được bị bỏ qua và ĐẾM theo lý do.
Kỳ vọng ghi theo hành vi đúng mong muốn của SmartHomeButler, không theo hành vi hiện tại.

Dùng::

    git clone https://github.com/OHF-Voice/intents.git /tmp/intents
    git -C /tmp/intents checkout b1e9b989
    .venv/bin/python -m evals.convert_ha_vi /tmp/intents

Câu test của HA theo giấy phép CC-BY-4.0 (ghi nguồn trong đầu file dataset).
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent
MAPPING = ROOT / "datasets" / "ha_vi_mapping.yaml"
OUTPUT = ROOT / "datasets" / "ha_vi.jsonl"

ANSWER_INTENTS = {
    "HassGetState", "HassClimateGetTemperature", "HassGetWeather", "HassGetCurrentTime",
    "HassGetCurrentDate",
}
# Finding 02, đợt 1: chưa có bộ hẹn giờ → trả lời “chưa hẹn giờ được”, không thực thi.
TIMER_INTENTS = {
    "HassStartTimer", "HassCancelTimer", "HassCancelAllTimers", "HassPauseTimer",
    "HassUnpauseTimer", "HassTimerStatus",
}
OUT_OF_SCOPE_INTENTS = {"HassShoppingListAddItem", "HassListAddItem"}
DEVICE_ONLY_INTENTS = {  # hệ thống chưa có tên hành động riêng → chỉ kiểm tra đúng thiết bị
    "HassMediaPause", "HassMediaUnpause", "HassMediaNext", "HassVacuumReturnToBase", "HassVacuumCleanArea",
}

_ON = {"curtain": "open", "window": "open", "door_lock": "lock"}
_OFF = {"curtain": "close", "window": "close", "door_lock": "unlock"}
# “ở đây / chỗ này”: HA lấy khu vực từ thiết bị đang nghe; bộ đo không có vị trí người nói,
# nên hỏi lại là đúng. “tất cả / cả nhà / mọi nơi” không kèm phòng = toàn bộ nhà.
_HERE = re.compile(r"(?<!\w)(ở đây|chỗ này|phòng này|khu vực này)(?!\w)")
_WHOLE_HOUSE = re.compile(r"(?<!\w)(tất cả|toàn bộ nhà|cả nhà|khắp nhà|mọi nơi)(?!\w)")
_CANCEL_TIMER_INTENTS = {"HassCancelTimer", "HassCancelAllTimers"}
_PRE_STATE = {
    "turn_on": {"power": "off"}, "turn_off": {"power": "on"}, "open": {"position": 0},
    "close": {"position": 100}, "lock": {"locked": False}, "unlock": {"locked": True},
}


class SkipCaseError(Exception):
    """Ca không chuyển được; thông điệp là lý do để đếm."""


class NotInHomeError(SkipCaseError):
    """Câu nhắm tới thiết bị, loại thiết bị hoặc khu vực mà nhà demo KHÔNG có. Không bỏ:
    đây là “lệnh không hợp lệ” (như HomeBench) — đúng là báo không có, không thực thi gì."""


def _norm(text: str) -> str:
    return " ".join(str(text).lower().split())


def _catalog() -> dict[str, dict[str, Any]]:
    os.environ.setdefault("LLM_DISABLED", "true")
    sys.path.insert(0, str(ROOT.parent))
    from src.iot.registry import DEVICE_SPECS

    return {
        s.slug: {"room": s.room, "type": getattr(s.device_type, "value", str(s.device_type)),
                 "aliases": [_norm(a) for a in (s.aliases or ())] + [_norm(s.name)]}
        for s in DEVICE_SPECS
    }


class Mapper:
    def __init__(self, mapping: dict[str, Any], catalog: dict[str, dict[str, Any]]):
        self.areas = {_norm(k): v for k, v in mapping["areas"].items()}
        self.entities = {_norm(k): v for k, v in mapping["entities"].items()}
        self.domains = mapping["domains"]
        self.cover_classes = mapping["cover_device_classes"]
        self.catalog = catalog

    def area(self, name: str) -> dict[str, Any]:
        key = _norm(name)
        if key not in self.areas:
            raise SkipCaseError(f"khu vực chưa xét trong bảng ánh xạ: {name}")
        if self.areas[key] is None:
            raise NotInHomeError(f"khu vực không có trong nhà demo: {name}")
        return dict(self.areas[key])

    def entity(self, name: str, area: str | None) -> dict[str, Any]:
        for key in ([f"{_norm(name)}|{_norm(area)}"] if area else []) + [_norm(name)]:
            if key in self.entities:
                if self.entities[key] is None:
                    raise NotInHomeError(f"thiết bị không có trong nhà demo: {name}")
                return dict(self.entities[key])
        raise SkipCaseError(f"thiết bị chưa xét trong bảng ánh xạ: {name}")

    def types(self, domain: str | None, device_class: str | None) -> list[str]:
        if domain == "cover" and device_class:
            types = self.cover_classes.get(device_class, "missing")
        else:
            types = self.domains.get(domain or "", "missing")
        if types == "missing":
            raise SkipCaseError(f"loại thiết bị chưa xét: {domain}/{device_class}")
        if types is None:
            raise NotInHomeError(f"loại thiết bị không có trong nhà demo: {domain}/{device_class}")
        return list(types)

    def devices_in(self, room: str | None, types: list[str]) -> list[str]:
        return sorted(s for s, d in self.catalog.items() if d["type"] in types and (room is None or d["room"] == room))

    def rewrite(self, sentence: str, entities: list[dict[str, Any]], areas: list[str]) -> str:
        """Thay tên thiết bị rồi tên khu vực có ``text``; dài trước, không thay chồng."""
        holders: list[str] = []
        out = sentence
        for ent in sorted(entities, key=lambda e: -len(e["name"])):
            try:
                text = self.entity(ent["name"], ent.get("area")).get("text")
            except SkipCaseError:
                continue
            if not text:
                continue
            pattern = re.compile(rf"(?<!\w){re.escape(ent['name'])}(?!\w)", re.IGNORECASE)
            if pattern.search(out):
                holders.append(text)
                out = pattern.sub(f"\x00{len(holders) - 1}\x00", out)
        for name in sorted(areas, key=len, reverse=True):
            info = self.areas.get(_norm(name))
            if not info or not info.get("text"):
                continue
            pattern = re.compile(rf"(?<!\w){re.escape(name)}(?!\w)", re.IGNORECASE)
            if pattern.search(out):
                holders.append(info["text"])
                out = pattern.sub(f"\x00{len(holders) - 1}\x00", out)
        return re.sub(r"\x00(\d+)\x00", lambda m: holders[int(m.group(1))], out)


def _turn_action(intent: str, device_type: str) -> str:
    if intent == "HassTurnOn":
        return _ON.get(device_type, "turn_on")
    return _OFF.get(device_type, "turn_off")


def expectation(
    intent: str, slots: dict[str, Any], entities: list[dict[str, Any]], m: Mapper, *, text: str, stem: str
) -> tuple[dict, dict, list[str]]:
    """Trả (expect, state ban đầu, tag) cho một câu test của HA (``text`` là câu đã ánh xạ)."""
    lowered = text.lower()
    if intent in ANSWER_INTENTS:
        return {"outcome": "answer", "actions": []}, {}, ["answer"]
    if intent in TIMER_INTENTS:
        # Huỷ hẹn giờ khi chưa có hẹn giờ nào: trả lời hoặc báo đã huỷ đều vô hại.
        outcome: Any = ["answer", "cancelled"] if intent in _CANCEL_TIMER_INTENTS else "answer"
        return {"outcome": outcome, "actions": []}, {}, ["timer", "known_bug"]
    if intent in OUT_OF_SCOPE_INTENTS:
        return {"outcome": "answer", "actions": []}, {}, ["out_of_scope"]
    if intent == "HassNevermind":
        return {"outcome": ["cancelled", "answer"], "actions": []}, {}, ["cancel"]

    by_name = {_norm(e["name"]): e for e in entities}
    room = None
    if slots.get("area"):
        room = m.area(str(slots["area"]))["room"]
    if slots.get("name"):
        ent = by_name.get(_norm(slots["name"]))
        if ent is None:
            raise SkipCaseError(f"slot name không có trong entities của file: {slots['name']}")
        if ent["domain"] in ("scene", "script"):
            raise NotInHomeError(f"nhà demo không có {ent['domain']}")
        targets = [m.entity(ent["name"], ent.get("area"))["device"]]
    else:
        if intent in ("HassClimateSetTemperature",):
            types = ["air_conditioner"]
        elif intent in ("HassLightSet",):
            types = ["light"]
        elif intent.startswith("HassVacuum"):
            types = ["vacuum"]
        elif intent.startswith("HassMedia") or intent == "HassSetVolume":
            if room is None:
                raise SkipCaseError("lệnh media không nêu thiết bị")
            raise SkipCaseError("lệnh media theo khu vực: HA chọn thiết bị đang phát, nhà demo không giả lập việc phát")
        else:
            types = m.types(slots.get("domain"), slots.get("device_class"))
        if room is None and (stem.startswith("context_") or _HERE.search(lowered)):
            return {"outcome": "clarification", "actions": []}, {}, ["device_control", "ambiguous_room"]
        targets = m.devices_in(room, types)
        if not targets:
            raise NotInHomeError(f"khu vực {room} không có thiết bị loại {types}")
        rooms = {m.catalog[t]["room"] for t in targets}
        if room is None and len(rooms) > 1 and not _WHOLE_HOUSE.search(lowered):
            # Không nêu phòng, nhà demo có loại này ở nhiều phòng → hỏi lại là đúng.
            return {"outcome": "clarification", "actions": []}, {}, ["device_control", "ambiguous_room"]

    tags = ["device_control"]
    state: dict[str, dict[str, Any]] = {}
    acts: list[dict[str, Any]] = []
    for dev in targets:
        dtype = m.catalog[dev]["type"]
        if intent in ("HassTurnOn", "HassTurnOff"):
            act: dict[str, Any] = {"device_id": dev, "action": _turn_action(intent, dtype)}
            state[dev] = dict(_PRE_STATE[act["action"]])
        elif intent == "HassLightSet":
            act = {"device_id": dev, "action": "set"}
            if "brightness" in slots:
                act["params"] = {"percent": int(slots["brightness"])}
            state[dev] = {"power": "on", "brightness": 70 if int(slots.get("brightness", 0)) != 70 else 40}
        elif intent == "HassClimateSetTemperature":
            act = {"device_id": dev, "action": "set", "params": {"temperature": float(slots["temperature"])}}
        elif intent == "HassSetPosition":
            act = {"device_id": dev, "action": "set", "params": {"percent": int(slots["position"])}}
        elif intent == "HassSetVolume":
            act = {"device_id": dev, "action": "set", "params": {"percent": int(slots["volume_level"])}}
            state[dev] = {"power": "on"}
        elif intent == "HassVacuumStart":
            act = {"device_id": dev, "action": "turn_on"}
            state[dev] = {"power": "off"}
        elif intent in DEVICE_ONLY_INTENTS:
            act = {"device_id": dev}
            state[dev] = {"power": "on"}
            tags.append("device_only")
        else:
            raise SkipCaseError(f"intent chưa hỗ trợ: {intent}")
        acts.append(act)
    expect: dict[str, Any] = {"outcome": "candidate_plan", "actions": acts}
    if room is not None and len(targets) > 1:
        # “vô hiệu hoá đèn bếp”: vừa là “mọi đèn trong bếp” vừa là tên thiết bị “Đèn bếp”.
        named = [a for a in acts if any(re.search(rf"(?<!\w){re.escape(al)}(?!\w)", lowered)
                                        for al in m.catalog[a["device_id"]]["aliases"])]
        if len(named) == 1:
            expect = {"any_of": [expect, {"outcome": "candidate_plan", "actions": named}]}
            tags.append("two_readings")
    return expect, state, tags


def convert(
    tests_dir: Path, m: Mapper
) -> tuple[list[dict[str, Any]], collections.Counter[str], collections.Counter[str], int]:
    cases: list[dict[str, Any]] = []
    skipped: collections.Counter[str] = collections.Counter()
    invalid: collections.Counter[str] = collections.Counter()
    total = 0
    for path in sorted(tests_dir.rglob("*.yaml")):
        intent = path.parent.name
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        entities = doc.get("entities") or []
        areas = [a["name"] for a in doc.get("areas") or []]
        for b_idx, block in enumerate(doc.get("tests") or []):
            slots = block.get("slots") or {}
            sentences = block.get("sentences") or []
            total += len(sentences)
            for s_idx, sentence in enumerate(sentences):
                text = m.rewrite(sentence, entities, areas)
                reason = None
                try:
                    expect, state, tags = expectation(intent, slots, entities, m, text=text, stem=path.stem)
                except NotInHomeError as exc:
                    reason = str(exc)
                    invalid[reason] += 1
                    expect, state, tags = {"outcome": ["answer", "no_action"], "actions": []}, {}, ["invalid_target"]
                except SkipCaseError as exc:
                    skipped[str(exc)] += 1
                    continue
                case = {
                    "id": f"ha-vi/{intent}/{path.stem}/{b_idx}/{s_idx}",
                    "tags": ["ha_vi", intent, *tags],
                    "text": text,
                    "expect": expect,
                    "source": {"file": f"tests/vi/{intent}/{path.name}", "sentence": sentence, "slots": slots,
                               "rewritten": text != sentence,
                               **({"invalid_reason": reason} if reason else {})},
                }
                if state:
                    case["state"] = state
                cases.append(case)
    return cases, skipped, invalid, total


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("intents_repo", type=Path, help="Thư mục clone của OHF-Voice/intents")
    p.add_argument("--output", type=Path, default=OUTPUT)
    args = p.parse_args(argv)

    tests_dir = args.intents_repo / "tests" / "vi"
    commit = subprocess.run(["git", "-C", str(args.intents_repo), "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True).stdout.strip() or "không rõ"
    mapper = Mapper(yaml.safe_load(MAPPING.read_text(encoding="utf-8")), _catalog())
    cases, skipped, invalid, total = convert(tests_dir, mapper)

    header = [
        "# Sinh tự động bởi evals/convert_ha_vi.py — KHÔNG sửa tay; sửa bảng ánh xạ rồi chạy lại.",
        f"# Nguồn: OHF-Voice/intents (https://github.com/OHF-Voice/intents) commit {commit}, tests/vi.",
        "# Giấy phép câu gốc: CC-BY-4.0 (https://creativecommons.org/licenses/by/4.0/). Câu đã được",
        "# ánh xạ tên thiết bị/khu vực sang nhà demo (xem trường source và ha_vi_mapping.yaml).",
        f"# Tổng {total} câu · chuyển được {len(cases)} (trong đó {sum(invalid.values())} là lệnh không hợp lệ,"
        f" tag invalid_target) · bỏ qua {total - len(cases)}.",
        "# Lệnh không hợp lệ (nhà demo không có; đúng là báo không có, không thực thi):",
        *[f"#   {n:3d}  {reason}" for reason, n in invalid.most_common()],
        "# Bỏ qua (không chấm được một cách công bằng):",
        *[f"#   {n:3d}  {reason}" for reason, n in skipped.most_common()],
    ]
    with args.output.open("w", encoding="utf-8") as fh:
        fh.write("\n".join(header) + "\n")
        for case in cases:
            fh.write(json.dumps(case, ensure_ascii=False) + "\n")
    print("\n".join(line[2:] for line in header[4:]))
    print(f"→ {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
