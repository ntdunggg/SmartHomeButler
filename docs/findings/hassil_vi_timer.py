"""Nguyên mẫu: mẫu câu "hẹn giờ chứa lệnh" tiếng Việt chạy trên hassil của Home Assistant.

KHÔNG dùng .venv của repo — chạy trong venv riêng:
  python3.11 -m venv /tmp/hassil-venv
  /tmp/hassil-venv/bin/pip install hassil home-assistant-intents
  /tmp/hassil-venv/bin/python -I docs/findings/hassil_vi_timer.py
Đã chạy với hassil 3.12.1, home-assistant-intents 2026.10.27.
"""
from hassil import Intents, recognize_all
from hassil.intents import TextSlotList
from home_assistant_intents import get_intents

data = get_intents("vi")
base = Intents.from_dict(data)

CMD = "{timer_command:conversation_command}"  # wildcard, đã có sẵn trong lists
UNITS = ("{timer_minutes:minutes} phút", "{timer_hours:hours} tiếng", "{timer_seconds:seconds} giây")
new = [{"sentences": [
    f"[sau|khoảng] {u} nữa [thì|hãy] {CMD}",
    f"{CMD} (sau|trong vòng) {u} [nữa]",
    f"{CMD} {u} nữa",
    f"hẹn giờ {u} [nữa] [rồi|thì] {CMD}",
    u,  # câu trần: "10 phút" → hẹn giờ không kèm lệnh
]} for u in UNITS]
data["intents"]["HassStartTimer"]["data"].extend(new)
ext = Intents.from_dict(data)

SLOTS = {"area": TextSlotList.from_strings(["phòng khách", "bếp"]),
         "name": TextSlotList.from_tuples([("đèn trần", "light.den_tran", {"domain": "light"})])}
TESTS = [
    "10 phút nữa tắt đèn trần trong bếp", "sau 10 phút nữa thì tắt đèn trần trong bếp",
    "tắt đèn trần trong bếp sau 10 phút", "tắt đèn trần trong bếp 10 phút nữa",
    "2 tiếng nữa tắt điều hoà", "hẹn giờ 30 phút tắt quạt", "mười phút nữa tắt đèn trần trong bếp",
    "bật quạt 30 phút", "tắt đèn trần trong bếp trong 10 phút", "2 giờ nữa tắt đèn", "7 giờ tối tắt đèn",
    "lát nữa tắt đèn", "5 phút nữa nhắc tôi uống thuốc",
    "10 phút", "mười phút", "2 tiếng", "đặt hẹn giờ 10 phút", "10 độ",
]

def show(intents, text):
    rs = list(recognize_all(text, intents, slot_lists=SLOTS))
    out = sorted({(r.intent.name, tuple(sorted((k, v.value) for k, v in r.entities.items()))) for r in rs})
    return out or "KHÔNG KHỚP"

for t in TESTS:
    print(f"{t!r:46} gốc: {str(show(base, t))[:50]:50} | thêm mẫu: {show(ext, t)}")
