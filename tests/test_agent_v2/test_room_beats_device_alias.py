"""Phòng NÊU TƯỜNG MINH thắng khớp alias thiết bị (§5, chẩn đoán registry-first).

"Bật đèn trần phòng bếp": alias "đèn trần" chỉ đăng ký cho đèn chùm Phòng khách. Bộ lọc
phòng cũ chỉ chạy khi có ≥2 alias khớp, nên câu này ground thẳng vào một thiết bị ở phòng
người dùng KHÔNG hề nhắc — rò ngữ cảnh ngay từ lượt đầu.

Ranh giới quan trọng: chỉ phòng nêu tường minh mới có quyền phủ quyết. `focus_room` ngầm
(vị trí người nói) chỉ là ưu tiên mềm, nếu không "loa bếp" nói từ phòng khách sẽ bị vứt.
"""

from __future__ import annotations

from src.agent.text import TextView
from src.iot.registry import spec_for
from src.nlu.normalizer import _match_devices, analyze


def test_named_room_vetoes_a_device_alias_that_lives_elsewhere():
    devices = analyze("Bật đèn trần phòng bếp.").matched_device_ids

    assert "den_chum_phong_khach" not in devices
    assert all(spec_for(d).room == "Phòng bếp" for d in devices)


def test_named_room_still_keeps_devices_that_are_inside_it():
    devices = analyze("Bật đèn bếp.", focus_room="Phòng bếp").matched_device_ids

    assert "den_bep" in devices


def test_an_implicit_focus_room_only_prefers_and_never_discards():
    """Nói từ Phòng khách nhưng gọi tên thiết bị ở bếp — vẫn phải giữ đúng thiết bị đó."""
    devices = analyze("Tăng loa bếp lên.", focus_room="Phòng khách").matched_device_ids

    assert devices == ("loa_bep",)


def test_matcher_contract_distinguishes_explicit_from_inferred_rooms():
    view = TextView.of("tăng loa bếp lên")

    assert _match_devices(view, ("Phòng khách",), room_explicit=False) == ("loa_bep",)
    assert _match_devices(view, ("Phòng khách",), room_explicit=True) == ()
