"""Đọc/ghi ba tầng bộ nhớ trên DB, và quy tụ (consolidate) bằng chứng thành hồ sơ dài hạn.

Phân vai:
- short-term  → `session_store.SessionStore` (Pending Intent Stack, đã có sẵn).
- episodic    → bảng `episodic_memories`: từng tình huống có thật kèm phản ứng người dùng.
- long-term   → bảng `resident_profiles` (mệnh đề ngữ nghĩa) + `habits` (bộ ba thiết bị/
  hành động/giờ suy từ ActionLog, đã có sẵn ở `habits.py`).

Ranh giới quan trọng: module này KHÔNG suy luận và KHÔNG quyết định gì. Nó nạp bằng chứng
cho `retrieval.py` chấm điểm, và ghi lại điều đã xảy ra. Việc nâng một quan sát thành thói
quen dài hạn có ngưỡng bằng chứng rõ ràng — một lần xảy ra không phải thói quen.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core import clock
from src.domain.models import EpisodicMemory, Habit, ResidentProfile
from src.memory.types import KIND_EPISODE, KIND_HABIT, KIND_TRAIT, MemoryItem, RoutineCandidate

logger = logging.getLogger("memory.store")

LOOKBACK_DAYS = 60
# Số NGÀY riêng biệt phải quan sát được trước khi một tình huống thành hồ sơ dài hạn.
# Đếm theo ngày (không theo lượt) để một buổi tối nghịch mười lần không thành thói quen.
MIN_EVIDENCE_DAYS = 3
# Dưới ngưỡng này thì hồ sơ tồn tại nhưng chưa đủ tin để đưa vào suy luận.
MIN_TRAIT_CONFIDENCE = 0.5

_POSITIVE_OUTCOMES = {"accepted", "executed"}
_NEGATIVE_OUTCOMES = {"rejected"}


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _tokens(text: str) -> tuple[str, ...]:
    """Token thô dùng làm bề mặt khớp truy hồi. Không có từ điển/stopword cố định — chỉ bỏ
    token quá ngắn, để không lén cài một bảng từ khoá vào tầng bộ nhớ.

    FOLD BỎ DẤU (§2026-08-10) ĐỒNG NHẤT với `retrieval.situation_tokens_from`: lượt truy hồi
    có thể gõ không dấu/typo nhẹ; nếu store giữ dấu còn query fold (hoặc ngược lại) thì hai
    bề mặt không bao giờ trùng. Fold cả hai để so khớp bền với biến thể dấu."""
    from src.agent.text import strip_diacritics

    cleaned = "".join(ch if ch.isalnum() else " " for ch in strip_diacritics(text.lower()))
    return tuple(sorted({t for t in cleaned.split() if len(t) > 2}))


# ---------------------------------------------------------------------------
# Ghi episodic
# ---------------------------------------------------------------------------
def record_episode(
    session: Session,
    *,
    household_id: int,
    user_id: int | None,
    utterance: str,
    goal_description: str = "",
    utterance_type: str = "",
    situation_label: str = "",
    room: str | None = None,
    now: datetime | None = None,
    proposed_actions: list[dict[str, Any]] | None = None,
    signals: dict[str, Any] | None = None,
    outcome: str = "proposed",
) -> EpisodicMemory:
    """Ghi lại một lượt tương tác. Gọi sau khi đã có kế hoạch (hoặc đã biết kết quả)."""
    now = now or clock.now()
    episode = EpisodicMemory(
        household_id=household_id,
        user_id=user_id,
        utterance=utterance[:2000],
        goal_description=goal_description[:2000],
        utterance_type=utterance_type,
        situation_label=situation_label[:120],
        room=room,
        # Giờ theo múi giờ ĐỊA PHƯƠNG (VN) để khớp với Habit.hour và now_hour ở retrieval,
        # và để part_of_day ra đúng buổi (22h tối, không phải 15h chiều).
        hour=clock.to_local(now).hour,
        signals=signals or {},
        proposed_actions=proposed_actions or [],
        outcome=outcome,
        created_at=now,
    )
    session.add(episode)
    session.flush()
    return episode


def update_outcome(session: Session, episode_id: int, *, outcome: str, correction_note: str = "") -> None:
    """Cập nhật phản ứng của người dùng cho một lượt đã ghi (accept/reject/correct/execute)."""
    episode = session.get(EpisodicMemory, episode_id)
    if episode is None:
        return
    episode.outcome = outcome
    if correction_note:
        episode.correction_note = correction_note[:2000]


# ---------------------------------------------------------------------------
# Nạp bằng chứng cho retrieval
# ---------------------------------------------------------------------------
def load_candidates(
    session: Session,
    *,
    household_id: int,
    user_id: int | None = None,
    now: datetime | None = None,
    lookback_days: int = LOOKBACK_DAYS,
) -> list[MemoryItem]:
    """Nạp episodic + long-term của hộ thành `MemoryItem` cho tầng chấm điểm.

    KHÔNG lọc theo phòng/tình huống ở đây: việc chọn là của `retrieval.retrieve`, và trộn
    hai chỗ lọc sẽ khiến điểm số không còn phản ánh đúng cái gì đã bị loại."""
    now = now or clock.now()
    since = now - timedelta(days=lookback_days)
    items: list[MemoryItem] = []

    episodes = session.scalars(
        select(EpisodicMemory).where(
            EpisodicMemory.household_id == household_id,
            EpisodicMemory.created_at >= since,
            # Cận trên: nhảy đồng hồ LÙI không được kéo ký ức "tương lai" vào recall (#12).
            EpisodicMemory.created_at <= now,
        )
    ).all()
    for ep in episodes:
        if ep.outcome == "proposed":
            continue  # chưa biết người dùng nghĩ gì → chưa phải bằng chứng
        polarity = "negative" if ep.outcome in _NEGATIVE_OUTCOMES else "positive"
        age = max(0.0, (now - _as_utc(ep.created_at)).total_seconds() / 86400.0)
        devices = tuple(str(d) for d in (ep.signals or {}).get("devices", []))
        verb = "đã đồng ý" if polarity == "positive" else "đã từ chối"
        note = f" (ghi chú: {ep.correction_note})" if ep.correction_note else ""
        items.append(
            MemoryItem(
                kind=KIND_EPISODE,
                text=f"Lần trước khi bạn nói «{ep.utterance}», mục tiêu được hiểu là «{ep.goal_description}» và bạn {verb}{note}.",
                # Một lượt đơn lẻ là bằng chứng YẾU — không được ngang hàng hồ sơ đã quy tụ.
                confidence=0.6 if polarity == "positive" else 0.7,
                polarity=polarity,
                room=ep.room,
                hour=ep.hour,
                devices=devices,
                situation_label=ep.situation_label,
                # Khớp truy hồi trên NGÔN NGỮ NGƯỜI DÙNG (câu cũ + mô tả mục tiêu cũ),
                # không trên nhãn tình huống snake_case.
                match_tokens=_tokens(f"{ep.utterance} {ep.goal_description}"),
                source_id=ep.user_id,
                age_days=age,
            )
        )

    traits = session.scalars(
        select(ResidentProfile).where(
            ResidentProfile.household_id == household_id,
            ResidentProfile.confidence >= MIN_TRAIT_CONFIDENCE,
        )
    ).all()
    for tr in traits:
        last = _as_utc(tr.last_observed_at) if tr.last_observed_at else now
        items.append(
            MemoryItem(
                kind=KIND_TRAIT,
                text=tr.statement_vi,
                confidence=tr.confidence,
                room=(tr.value or {}).get("room"),
                situation_label=(tr.value or {}).get("situation", ""),
                trait_key=tr.trait_key,
                value=dict(tr.value or {}),
                match_tokens=_tokens(f"{tr.statement_vi} {(tr.value or {}).get('utterance', '')}"),
                source_id=tr.user_id,
                age_days=max(0.0, (now - last).total_seconds() / 86400.0),
            )
        )

    habits = session.scalars(
        select(Habit).where(
            Habit.household_id == household_id,
            Habit.confidence >= MIN_TRAIT_CONFIDENCE,
            # Thói quen người dùng đã bỏ thì không được làm bằng chứng cho suy luận nữa —
            # nếu không, nó vẫn lái agent dù đã ngừng sinh đề xuất.
            Habit.enabled.is_(True),
        )
    ).all()
    for hb in habits:
        items.append(
            MemoryItem(
                kind=KIND_HABIT,
                text=hb.description_vi,
                confidence=hb.confidence,
                hour=hb.hour,
                devices=(hb.device_slug,),
                trait_key=f"habit:{hb.device_slug}:{hb.action}:{hb.hour}",
                value={"device": hb.device_slug, "action": hb.action},
                source_id=hb.user_id,
            )
        )

    return items


# ---------------------------------------------------------------------------
# Quy tụ bằng chứng → hồ sơ dài hạn
# ---------------------------------------------------------------------------
def consolidate(
    session: Session,
    *,
    household_id: int,
    now: datetime | None = None,
    min_evidence_days: int = MIN_EVIDENCE_DAYS,
) -> list[ResidentProfile]:
    """Nâng các tình huống LẶP LẠI thành mệnh đề hồ sơ dài hạn.

    Ngưỡng là số NGÀY riêng biệt, và confidence = thuận / (thuận + nghịch) — nên một tình
    huống bị từ chối nhiều lần sẽ tự tụt xuống dưới ngưỡng dùng được thay vì phải xoá tay.
    """
    now = now or clock.now()
    since = now - timedelta(days=LOOKBACK_DAYS)

    episodes = session.scalars(
        select(EpisodicMemory).where(
            EpisodicMemory.household_id == household_id,
            EpisodicMemory.created_at >= since,
            # Cận trên: quy tụ không được gom bằng chứng "tương lai" khi nhảy đồng hồ lùi (#12).
            EpisodicMemory.created_at <= now,
            EpisodicMemory.situation_label != "",
        )
    ).all()

    # (user_id, situation_label, room) → bằng chứng
    buckets: dict[tuple[int | None, str, str | None], dict[str, Any]] = {}
    for ep in episodes:
        if ep.outcome not in _POSITIVE_OUTCOMES and ep.outcome not in _NEGATIVE_OUTCOMES:
            continue
        key = (ep.user_id, ep.situation_label, ep.room)
        bucket = buckets.setdefault(
            key, {"pos_days": set(), "neg": 0, "ids": [], "last": None, "utterance": "", "goal": None, "hours": []}
        )
        # Giữ một câu nói ĐẠI DIỆN bằng tiếng Việt: hồ sơ cần bề mặt khớp cùng ngôn ngữ với
        # người dùng, còn `situation_label` chỉ là khoá gom nhóm.
        if not bucket["utterance"] and ep.utterance:
            bucket["utterance"] = ep.utterance
        if ep.outcome in _POSITIVE_OUTCOMES:
            # Ranh giới NGÀY theo giờ VN — hành động 00:00–07:00 VN không bị đếm nhầm
            # sang ngày UTC hôm trước (#14).
            bucket["pos_days"].add(clock.to_local(ep.created_at).date())
            # Chỉ học goal + giờ từ lượt ĐƯỢC CHẤP NHẬN: đó là CÁCH HIỂU người dùng đã đồng ý,
            # để tái dùng. Lượt bị từ chối không phải thói quen nên không đóng góp goal/giờ.
            if bucket["goal"] is None:
                g = (ep.signals or {}).get("goal")
                if g and g.get("desired_outcomes"):
                    bucket["goal"] = g
            if ep.hour is not None:
                bucket["hours"].append(int(ep.hour))
        else:
            bucket["neg"] += 1
        bucket["ids"].append(ep.id)
        created = _as_utc(ep.created_at)
        if bucket["last"] is None or created > bucket["last"]:
            bucket["last"] = created

    promoted: list[ResidentProfile] = []
    for (user_id, situation, room), bucket in buckets.items():
        pos = len(bucket["pos_days"])
        neg = int(bucket["neg"])
        if pos < min_evidence_days:
            continue  # chưa đủ bằng chứng → KHÔNG bịa ra thói quen từ vài lần
        confidence = pos / (pos + neg) if (pos + neg) else 0.0
        trait_key = f"situation:{situation}:{room or 'any'}"
        # `goal` (SemanticGoal mức capability) là thứ làm routine TÁI DÙNG được; `hours` là
        # histogram giờ để dựng lịch trình (Part C). Cả hai chỉ có khi lượt được chấp nhận.
        value: dict[str, Any] = {"situation": situation, "room": room, "utterance": bucket["utterance"]}
        if bucket["goal"]:
            value["goal"] = bucket["goal"]
        if bucket["hours"]:
            value["hours"] = _hour_histogram(bucket["hours"])
        profile = _upsert_trait(
            session,
            household_id=household_id,
            user_id=user_id,
            trait_key=trait_key,
            kind="routine",
            statement_vi=(
                f"Khi bạn nói «{bucket['utterance'] or situation}»"
                + (f" ở {room}" if room else "")
                + ", bạn thường chấp nhận đề xuất của mình."
            ),
            value=value,
            evidence_count=pos,
            contradiction_count=neg,
            confidence=confidence,
            evidence_ids=list(bucket["ids"])[-20:],
            last_observed_at=bucket["last"],
        )
        promoted.append(profile)

    return promoted


def _hour_histogram(hours: list[int]) -> dict[str, int]:
    """Đếm số lần quan sát theo giờ (khoá là chuỗi để JSON-hoá được)."""
    hist: dict[str, int] = {}
    for h in hours:
        key = str(int(h))
        hist[key] = hist.get(key, 0) + 1
    return hist


# ---------------------------------------------------------------------------
# Nạp routine đã học để TÁI DÙNG (tách hẳn khỏi đường bằng chứng ở trên)
# ---------------------------------------------------------------------------
def load_routines(
    session: Session,
    *,
    household_id: int,
    user_id: int | None = None,
    now: datetime | None = None,
) -> list[RoutineCandidate]:
    """Nạp các thói quen đã học (ResidentProfile kind='routine' CÓ goal tái dùng được).

    Tách khỏi `load_candidates`: đường kia trả BẰNG CHỨNG (đã cố tình bỏ hành động), đường
    này trả CÁCH HIỂU tái dùng được — hai việc khác nhau, giữ riêng để không trộn lẫn.

    Scope theo người nói: lấy routine của CHÍNH người này cộng routine dùng chung cả hộ
    (`user_id is None`). Ngưỡng confidence để một routine chưa đủ tin không tự nhảy vào."""
    now = now or clock.now()
    rows = session.scalars(
        select(ResidentProfile).where(
            ResidentProfile.household_id == household_id,
            ResidentProfile.kind == "routine",
            ResidentProfile.confidence >= MIN_TRAIT_CONFIDENCE,
        )
    ).all()

    out: list[RoutineCandidate] = []
    for tr in rows:
        if tr.user_id is not None and user_id is not None and tr.user_id != user_id:
            continue  # routine cá nhân của người KHÁC — không dùng cho lượt này
        value = tr.value or {}
        goal = value.get("goal")
        if not goal or not goal.get("desired_outcomes"):
            continue  # routine cũ chưa có goal tái dùng được → chỉ là bằng chứng, bỏ qua ở đây
        hours = tuple(int(h) for h in (value.get("hours") or {}))
        last = _as_utc(tr.last_observed_at) if tr.last_observed_at else now
        out.append(
            RoutineCandidate(
                trait_key=tr.trait_key,
                goal=goal,
                confidence=tr.confidence,
                room=value.get("room"),
                match_tokens=_tokens(f"{value.get('utterance', '')} {goal.get('goal_description', '')}"),
                hours=hours,
                user_id=tr.user_id,
                age_days=max(0.0, (now - last).total_seconds() / 86400.0),
            )
        )
    return out


def _upsert_trait(
    session: Session,
    *,
    household_id: int,
    user_id: int | None,
    trait_key: str,
    kind: str,
    statement_vi: str,
    value: dict[str, Any],
    evidence_count: int,
    contradiction_count: int,
    confidence: float,
    evidence_ids: list[int],
    last_observed_at: datetime | None,
) -> ResidentProfile:
    profile = session.scalar(
        select(ResidentProfile).where(
            ResidentProfile.household_id == household_id,
            ResidentProfile.user_id == user_id,
            ResidentProfile.trait_key == trait_key,
        )
    )
    if profile is None:
        profile = ResidentProfile(household_id=household_id, user_id=user_id, trait_key=trait_key)
        session.add(profile)
    profile.kind = kind
    profile.statement_vi = statement_vi
    profile.value = value
    profile.evidence_count = evidence_count
    profile.contradiction_count = contradiction_count
    profile.confidence = round(confidence, 3)
    profile.evidence_ids = evidence_ids
    profile.last_observed_at = last_observed_at
    session.flush()
    return profile
