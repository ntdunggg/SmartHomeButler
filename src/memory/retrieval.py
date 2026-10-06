"""Truy hồi ký ức — CHẤM ĐIỂM TẤT ĐỊNH, hàm thuần, không chạm DB.

Vì sao tất định: đây là bước quyết định agent "nhớ" ra cái gì trước khi suy luận. Nếu nó
cũng do model quyết thì không còn chỗ nào kiểm chứng được, và một lần truy hồi lệch sẽ kéo
theo cả mục tiêu lẫn kế hoạch lệch mà không để lại dấu vết. Ở đây mọi điểm số đều đến từ
tín hiệu quan sát được (phòng, thiết bị, khung giờ, độ mới, số bằng chứng), nên test được
và giải thích được.

Bất biến: truy hồi CHỈ trả về bằng chứng. Nó không chọn hành động, không xếp hạng kế hoạch,
và không bao giờ là lý do để bỏ qua Semantic Goal / Planner / Validator.
"""

from __future__ import annotations

from src.memory.types import KIND_EPISODE, MemoryItem, MemoryRecall, RoutineCandidate

# Trọng số các tín hiệu khớp. Cộng dồn rồi nhân với độ tin cậy của chính mẩu ký ức.
_W_ROOM = 0.30
_W_DEVICE = 0.25
_W_SITUATION = 0.25
_W_HOUR = 0.12
_W_SPEAKER = 0.08

# Ký ức cũ hơn ngưỡng này coi như hết liên quan (nửa đời của trọng số độ mới).
_RECENCY_HALFLIFE_DAYS = 21.0
# Dưới ngưỡng này thì không đáng đưa vào prompt — thà không nhớ gì còn hơn nhớ nhiễu.
_MIN_SCORE = 0.25
_MAX_ITEMS = 5


def _hour_affinity(item_hour: int | None, now_hour: int | None) -> float:
    """Gần giờ thì điểm cao, xa thì tắt dần. Vòng qua nửa đêm (23h và 1h chỉ cách 2 tiếng)."""
    if item_hour is None or now_hour is None:
        return 0.0
    diff = abs(item_hour - now_hour) % 24
    diff = min(diff, 24 - diff)
    if diff <= 1:
        return 1.0
    if diff <= 3:
        return 0.5
    return 0.0


def _recency_weight(age_days: float) -> float:
    """Giảm một nửa mỗi `_RECENCY_HALFLIFE_DAYS`. Không bao giờ về 0 hẳn — thói quen cũ
    vẫn là bằng chứng yếu, chỉ là không được lấn bằng chứng mới."""
    if age_days <= 0:
        return 1.0
    return 0.5 ** (age_days / _RECENCY_HALFLIFE_DAYS)


def _token_affinity(match_tokens: tuple[str, ...], situation_tokens: frozenset[str]) -> float:
    """Tỉ lệ token của ký ức cũ xuất hiện lại trong câu nói hiện tại (∈ [0, 1])."""
    if not situation_tokens or not match_tokens:
        return 0.0
    tokens = set(match_tokens)
    return len(tokens & situation_tokens) / len(tokens)


def _situation_affinity(item: MemoryItem, *, situation_tokens: frozenset[str]) -> float:
    """Trùng lặp từ khoá giữa NỘI DUNG ký ức cũ và câu nói hiện tại.

    Khớp trên `match_tokens` (câu nói + mô tả mục tiêu của lượt cũ, cùng ngôn ngữ với người
    dùng), KHÔNG khớp trên `situation_label` — nhãn đó do tầng ngữ nghĩa đặt bằng snake_case
    tiếng Anh nên không bao giờ trùng token với câu tiếng Việt.

    Đây là khớp BẰNG CHỨNG, không phải ánh xạ câu → kế hoạch: kết quả chỉ quyết định mẩu ký
    ức nào được đọc, còn hành động vẫn do Planner tổng hợp lại từ đầu."""
    return _token_affinity(item.match_tokens, situation_tokens)


def _is_topically_related(item: MemoryItem, *, situation_tokens: frozenset[str], devices: frozenset[str]) -> bool:
    """Ký ức phải liên quan tới ĐIỀU ĐANG NÓI, không chỉ tới CHỖ ĐANG ĐỨNG.

    Cùng phòng và cùng khung giờ là tín hiệu phụ trợ; một mình chúng không đủ. Không có
    luật này thì mọi ký ức trong phòng khách đều trồi lên cho mọi câu nói ở phòng khách —
    đo được: ký ức 'có khách' bị gọi ra khi người dùng chỉ nói 'hơi lạnh'."""
    if not item.match_tokens and not item.devices:
        return True  # ký ức không gắn chủ đề nào (vd sở thích chung) → để điểm số tự quyết
    if devices and item.devices and (devices & set(item.devices)):
        return True
    return bool(situation_tokens and set(item.match_tokens) & situation_tokens)


def score_item(
    item: MemoryItem,
    *,
    room: str | None,
    devices: frozenset[str],
    now_hour: int | None,
    situation_tokens: frozenset[str],
    same_speaker: bool,
) -> float:
    """Điểm liên quan trong [0, 1]. Hàm thuần, không phụ thuộc thứ tự gọi."""
    if not _is_topically_related(item, situation_tokens=situation_tokens, devices=devices):
        return 0.0

    raw = 0.0
    if room and item.room and item.room == room:
        raw += _W_ROOM
    if devices and item.devices:
        if devices & set(item.devices):
            raw += _W_DEVICE
    raw += _W_SITUATION * _situation_affinity(item, situation_tokens=situation_tokens)
    raw += _W_HOUR * _hour_affinity(item.hour, now_hour)
    if same_speaker:
        raw += _W_SPEAKER

    # Ký ức mà chính nó đã yếu (ít bằng chứng) không được lên cao chỉ vì trùng phòng.
    return raw * max(0.0, min(1.0, item.confidence)) * _recency_weight(item.age_days)


def _conflict_key(item: MemoryItem) -> str | None:
    """Hai mẩu nói về CÙNG một mệnh đề (cùng trait_key) thì phải nhất quán mới dùng được."""
    return item.trait_key or None


def _drop_conflicting(items: list[tuple[float, MemoryItem]]) -> tuple[list[tuple[float, MemoryItem]], list[str]]:
    """Cùng `trait_key` nhưng giá trị mâu thuẫn → BỎ CẢ HAI.

    Fail-closed: ký ức mâu thuẫn nghĩa là ta chưa thật sự biết người dùng muốn gì. Chọn
    bên điểm cao hơn chính là đoán — thứ kiến trúc này cấm."""
    by_key: dict[str, list[tuple[float, MemoryItem]]] = {}
    passthrough: list[tuple[float, MemoryItem]] = []
    for scored in items:
        key = _conflict_key(scored[1])
        if key is None:
            passthrough.append(scored)
        else:
            by_key.setdefault(key, []).append(scored)

    kept = list(passthrough)
    dropped: list[str] = []
    for key, group in by_key.items():
        values = {repr(sorted(m.value.items())) for _s, m in group}
        polarities = {m.polarity for _s, m in group}
        if len(values) > 1 or len(polarities) > 1:
            dropped.append(key)
            continue
        kept.append(max(group, key=lambda t: t[0]))
    return kept, dropped


def retrieve(
    candidates: list[MemoryItem],
    *,
    room: str | None = None,
    devices: frozenset[str] = frozenset(),
    now_hour: int | None = None,
    situation_tokens: frozenset[str] = frozenset(),
    speaker_matches: frozenset[int] = frozenset(),
    limit: int = _MAX_ITEMS,
    min_score: float = _MIN_SCORE,
) -> MemoryRecall:
    """Chọn các mẩu ký ức đáng đưa vào ngữ cảnh của lượt này.

    `speaker_matches` là tập source_id thuộc về chính người đang nói (đã lọc sẵn ở tầng
    store) — dùng để cộng điểm "cùng người", không phải để lọc cứng: thói quen của cả hộ
    vẫn là bằng chứng hợp lệ."""
    scored = [
        (
            score_item(
                item,
                room=room,
                devices=devices,
                now_hour=now_hour,
                situation_tokens=situation_tokens,
                same_speaker=item.source_id in speaker_matches if item.source_id is not None else False,
            ),
            item,
        )
        for item in candidates
    ]
    scored = [pair for pair in scored if pair[0] >= min_score]
    kept, dropped = _drop_conflicting(scored)
    kept.sort(key=lambda t: t[0], reverse=True)

    # Bằng chứng NGHỊCH (đã bị từ chối) luôn được giữ chỗ: nếu bị top-k cắt mất, agent sẽ
    # lặp lại đúng đề xuất người dùng vừa chối.
    top = kept[:limit]
    if not any(m.polarity == "negative" for _s, m in top):
        negatives = [pair for pair in kept if pair[1].polarity == "negative"]
        if negatives:
            top = top[: max(0, limit - 1)] + [negatives[0]]

    return MemoryRecall(items=tuple(m for _s, m in top), dropped_conflicts=tuple(dropped))


# Ngưỡng TÁI DÙNG cao hơn hẳn ngưỡng bằng chứng (_MIN_SCORE=0.25): ở đây ta THAY thế bước
# author-goal bằng mục tiêu đã học, nên phải chắc chắn hơn nhiều so với chỉ "nhớ ra để tham
# khảo". Dưới ngưỡng này thì cứ để LLM tự hiểu lại từ đầu — an toàn hơn là dùng lại nhầm.
_ROUTINE_MIN_SCORE = 0.55
_ROUTINE_MIN_CONFIDENCE = 0.5


def score_routine(
    routine: RoutineCandidate,
    *,
    room: str | None,
    now_hour: int | None,
    situation_tokens: frozenset[str],
) -> float:
    """Điểm khớp một routine với lượt hiện tại ∈ [0, 1]. Hàm thuần, tất định.

    Dùng lại đúng các tín hiệu của `score_item` (từ khoá câu nói, phòng, khung giờ) rồi nhân
    với độ tin cậy + độ mới của routine — không có scorer thứ hai để lệch chuẩn."""
    # Từ khoá là tín hiệu CHÍNH: một routine chỉ được tái dùng khi câu nói lần này thực sự
    # giống tình huống đã học, không phải chỉ vì cùng phòng/cùng giờ.
    token = _token_affinity(routine.match_tokens, situation_tokens)
    if token <= 0.0:
        return 0.0
    raw = _W_SITUATION * token
    if room and routine.room and routine.room == room:
        raw += _W_ROOM
    raw += _W_HOUR * _hour_affinity_hist(routine.hours, now_hour)
    # Chuẩn hoá về [0,1] theo tổng trọng số có thể đạt (situation + room + hour).
    raw /= _W_SITUATION + _W_ROOM + _W_HOUR
    return raw * max(0.0, min(1.0, routine.confidence)) * _recency_weight(routine.age_days)


def _hour_affinity_hist(hours: tuple[int, ...], now_hour: int | None) -> float:
    """Ái lực giờ khi routine có NHIỀU giờ quan sát — lấy giờ khớp nhất trong histogram."""
    if not hours or now_hour is None:
        return 0.0
    return max(_hour_affinity(h, now_hour) for h in hours)


def select_routine(
    routines: list[RoutineCandidate],
    *,
    room: str | None = None,
    now_hour: int | None = None,
    situation_tokens: frozenset[str] = frozenset(),
    min_score: float = _ROUTINE_MIN_SCORE,
    min_confidence: float = _ROUTINE_MIN_CONFIDENCE,
) -> RoutineCandidate | None:
    """Chọn MỘT routine đủ tốt để tái dùng, hoặc None.

    Trả None khi không đủ chắc — pipeline sẽ author mục tiêu như bình thường. Việc scope theo
    người nói (routine cá nhân vs cả hộ) đã làm ở tầng store (`load_routines`); ở đây chỉ còn
    chấm điểm khớp tình huống. Không bao giờ trả nhiều routine: tái dùng là thay thế một bước
    suy luận, phải dứt khoát một cách hiểu."""
    best: tuple[float, RoutineCandidate] | None = None
    for r in routines:
        if r.confidence < min_confidence:
            continue
        s = score_routine(r, room=room, now_hour=now_hour, situation_tokens=situation_tokens)
        if s < min_score:
            continue
        if best is None or s > best[0]:
            best = (s, r)
    return best[1] if best else None


def situation_tokens_from(text: str) -> frozenset[str]:
    """Tách token thô từ câu nói để khớp nhãn tình huống. Không có danh sách từ khoá cố
    định — chỉ bỏ token quá ngắn.

    FOLD BỎ DẤU (§2026-08-10): người dùng có thể gõ không dấu / sai chính tả nhẹ ở một lượt
    ("co khach" vs "có khách") — nếu không fold thì token không trùng `match_tokens` (vốn cũng
    được fold cùng cách ở store) và leg situation-affinity trượt oan. Phải fold ĐỒNG NHẤT với
    `src.memory.store._tokens` để hai bề mặt so khớp được."""
    from src.agent.text import strip_diacritics

    return frozenset(t for t in strip_diacritics(text.lower()).replace(",", " ").split() if len(t) > 2)


__all__ = [
    "KIND_EPISODE",
    "retrieve",
    "score_item",
    "score_routine",
    "select_routine",
    "situation_tokens_from",
]
