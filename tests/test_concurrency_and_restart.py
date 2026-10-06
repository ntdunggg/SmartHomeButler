"""Test Concurrency & Restart Persistence (FR-15 & FR-08)."""

from __future__ import annotations

import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from src.agent.preference.repository import SqlPreferenceRepository
from src.agent.preference.state_encoder import encode_state
from src.domain.models import (
    Base,
    EpisodicMemory,
    Household,
    LearningDecision,
    LearningFeedback,
    PreferenceQValue,
    User,
)
from src.models.schemas import FeedbackIn
from src.services.feedback_service import FeedbackService


def test_concurrent_feedback_atomic_idempotency():
    """Gửi đồng thời nhiều request với cùng 1 Idempotency-Key -> chỉ tạo đúng 1 receipt và 1 lần update Q-table."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_url = f"sqlite:///{db_path}"

    engine = None
    try:
        engine = create_engine(db_url, connect_args={"timeout": 15})
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine)

        with session_factory() as session:
            h = Household(name="Nhà Concurrency")
            session.add(h)
            session.flush()

            u = User(household_id=h.id, username="concur_user", password_hash="hash", role="owner")
            ep = EpisodicMemory(household_id=h.id, user_id=u.id, utterance="turn on ac", outcome="proposed")
            session.add_all([u, ep])
            session.flush()

            state = encode_state(resident=str(u.id), room="bedroom", now=datetime.now(UTC))
            dec = LearningDecision(
                execution_id="exec_concur_1",
                household_id=h.id,
                user_id=u.id,
                episode_id=ep.id,
                conversation_id="conv_c",
                rl_state=state.to_dict(),
                executed_actions=[{"device_slug": "ac", "action": "set_temperature", "params": {"temperature": 24}}],
            )
            session.add(dec)
            session.commit()
            user_id = u.id
            household_id = h.id

        payload = FeedbackIn(execution_id="exec_concur_1", outcome="accepted", dimension="temperature")

        def _send_fb():
            with session_factory() as s:
                user = s.get(User, user_id)
                return FeedbackService.handle_feedback(
                    s,
                    user=user,
                    payload=payload,
                    idempotency_key="key_concur_exact",
                )

        # Chạy 5 worker song song
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(_send_fb) for _ in range(5)]
            results = [f.result() for f in futures]

        # Xác minh: Tất cả kết quả trả về cùng 1 feedback_id
        fb_ids = {r.feedback_id for r in results}
        assert len(fb_ids) == 1

        # Có ít nhất 4 kết quả là duplicate=True (và 1 cái False ban đầu)
        duplicates = [r.duplicate for r in results]
        assert duplicates.count(True) >= 4

        with session_factory() as s:
            # Chỉ có 1 bản ghi LearningFeedback
            fb_count = s.query(LearningFeedback).filter_by(idempotency_key="key_concur_exact").count()
            assert fb_count == 1

            # Q-table chỉ update đúng 1 lần (update_count = 1)
            q_row = s.scalar(
                select(PreferenceQValue).where(
                    PreferenceQValue.household_id == household_id,
                    PreferenceQValue.user_id == user_id,
                    PreferenceQValue.dimension == "temperature",
                    PreferenceQValue.action == 24,
                )
            )
            assert q_row is not None
            assert q_row.update_count == 1
    finally:
        # Trên Windows, SQLite giữ handle file tới khi pool được dispose — phải đóng
        # engine TRƯỚC khi xoá file, nếu không os.remove ném WinError 32 (file đang bị giữ).
        if engine is not None:
            engine.dispose()
        if os.path.exists(db_path):
            os.remove(db_path)


def test_restart_persistence():
    """Dữ liệu decision, receipt và Q-table sống sót nguyên vẹn qua restart (đóng / mở DB session mới)."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_url = f"sqlite:///{db_path}"

    try:
        engine1 = create_engine(db_url)
        Base.metadata.create_all(engine1)
        session_factory1 = sessionmaker(bind=engine1)

        with session_factory1() as s1:
            h = Household(name="Nhà Restart")
            s1.add(h)
            s1.flush()
            u = User(household_id=h.id, username="res_user", password_hash="hash", role="owner")
            ep = EpisodicMemory(household_id=h.id, user_id=u.id, utterance="turn on", outcome="proposed")
            s1.add_all([u, ep])
            s1.flush()
            state = encode_state(resident=str(u.id), room="bedroom", now=datetime.now(UTC))
            dec = LearningDecision(
                execution_id="exec_res_1",
                household_id=h.id,
                user_id=u.id,
                episode_id=ep.id,
                conversation_id="conv_res",
                rl_state=state.to_dict(),
                executed_actions=[{"device_slug": "ac", "action": "set_temperature", "params": {"temperature": 23}}],
            )
            s1.add(dec)
            s1.commit()

            # Feedback
            FeedbackService.handle_feedback(
                s1,
                user=u,
                payload=FeedbackIn(execution_id="exec_res_1", outcome="accepted", dimension="temperature"),
                idempotency_key="key_res",
            )
            user_id = u.id
            household_id = h.id

        # Đóng engine 1, mở engine 2 hoàn toàn mới (mô phỏng restart server)
        engine1.dispose()

        engine2 = create_engine(db_url)
        session_factory2 = sessionmaker(bind=engine2)

        with session_factory2() as s2:
            # 1. Decision tồn tại
            d = s2.scalar(select(LearningDecision).where(LearningDecision.execution_id == "exec_res_1"))
            assert d is not None
            assert d.episode_id is not None

            # 2. Receipt tồn tại và status succeeded
            fb = s2.scalar(select(LearningFeedback).where(LearningFeedback.idempotency_key == "key_res"))
            assert fb is not None
            assert fb.rl_status == "succeeded"
            assert fb.memory_status == "succeeded"

            # 3. Preference Q-value tồn tại và tính toán distribution chính xác
            repo = SqlPreferenceRepository(s2)
            dist = repo.distribution(
                household_id=household_id,
                user_id=user_id,
                dimension="temperature",
                state=state,
            )
            assert dist is not None
            assert dist.confidence > 0.0
            assert dist.top()[0] == "23"

        engine2.dispose()
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)
