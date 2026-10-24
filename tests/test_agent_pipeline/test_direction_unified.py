"""Kiểm thử nguồn sự thật DUY NHẤT cho hướng tăng/giảm (`src/nlu/direction.py`, LS-154).

Chốt: (a) "<tính từ> lên/xuống/lại/đi" nhận diện được (trước đây turn_intent chỉ dò "<adj> hơn");
(b) hai consumer (turn_intent bare_verbs=True, understanding bare_verbs=False) NHẤT QUÁN về hướng;
(c) "lên"/"xuống" trần chỉ là động từ hướng ở bản bare (understanding phải để "xuống 24 độ"=set).
"""

from __future__ import annotations

import pytest

from src.nlu import understanding
from src.nlu.direction import direction_of


@pytest.mark.parametrize(
    "text, direction",
    [
        ("to lên", "increase"),
        ("nhỏ xuống", "decrease"),  # LS-154
        ("sáng hơn", "increase"),
        ("mát hơn", "decrease"),
        ("tối đi", "decrease"),
        ("nhỏ lại", "decrease"),
        ("mạnh hơn chút", "increase"),
        ("giảm bớt", "decrease"),
        ("tăng lên", "increase"),
    ],
)
def test_direction_of_bao_gom_adj_dinh_huong(text, direction):
    assert direction_of(text, bare_verbs=True)[0] == direction


@pytest.mark.parametrize("text", ["to lên", "nhỏ xuống", "sáng hơn", "mát hơn", "tối đi"])
def test_understanding_va_direction_nhat_quan(text):
    """Consumer NLU dùng trực tiếp nguồn `direction_of`, không giữ regex hướng thứ hai."""
    want = direction_of(text, bare_verbs=False)[0]
    got = understanding.direction_of(text, bare_verbs=False)[0]
    assert got == want, f"{text!r}: understanding={got} vs direction_of={want}"


def test_bare_verb_khac_nhau_giua_hai_consumer():
    """"xuống"/"lên" TRẦN: là hướng ở bản bare (turn_intent), KHÔNG ở bản non-bare (understanding)."""
    assert direction_of("xuống", bare_verbs=True)[0] == "decrease"
    assert direction_of("xuống", bare_verbs=False)[0] == ""
    assert direction_of("lên", bare_verbs=True)[0] == "increase"
    assert direction_of("lên", bare_verbs=False)[0] == ""
