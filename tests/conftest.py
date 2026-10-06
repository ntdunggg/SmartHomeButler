"""Fixture dùng chung cho test.

Mỗi test chạy trên một file SQLite riêng trong thư mục tạm và một IoT bus mới,
nên các case không ảnh hưởng lẫn nhau và không đụng tới dữ liệu thật.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.orm import Session

from src.agent.memory import embeddings
from src.config import get_settings
from src.db import session as db_session
from src.iot import factory
from src.iot.memory_bus import InMemoryBus
from src.memory import session_store


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch) -> Iterator[None]:
    """Cô lập DB, bus, bộ nhớ phiên và tắt LLM cho từng test."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("APP_ENV", "test")
    # Test không được gọi mạng: agent phải chạy hoàn toàn bằng NLU rule-based
    monkeypatch.setenv("LLM_DISABLED", "true")
    monkeypatch.setenv("AUTOMATION_ENABLED", "false")
    monkeypatch.setenv("JWT_SECRET", "test-secret-du-dai-cho-hs256-32-byte")
    monkeypatch.setenv("ENCRYPTION_MASTER_KEY", "test-master-key-du-dai-cho-hs256-32b")

    get_settings.cache_clear()
    db_session.reset_engine()
    factory.set_bus(InMemoryBus(latency=0))
    session_store.set_session_store(None)
    # Provider nhúng được nhớ ở phạm vi module; xoá để nó dựng lại theo settings của test
    # (LLM_DISABLED=true → hashing tất định) thay vì kế thừa provider của test trước.
    embeddings.set_provider(None)

    yield

    factory.set_bus(None)
    session_store.set_session_store(None)
    embeddings.set_provider(None)
    get_settings.cache_clear()
    db_session.reset_engine()


@pytest.fixture
def seeded() -> Iterator[Session]:
    """DB đã tạo bảng và seed hộ mẫu."""
    from src.db.seed import run_seed

    run_seed()
    with db_session.session_scope() as session:
        yield session


@pytest_asyncio.fixture
async def client(seeded) -> AsyncClient:
    """HTTP client gọi thẳng vào ASGI app, không cần dựng server."""
    from src.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def login(client: AsyncClient):
    """Trả về hàm lấy header Authorization cho một tài khoản seed."""

    async def _login(username: str = "bo") -> dict[str, str]:
        response = await client.post("/api/v1/auth/login", json={"username": username, "password": "demo1234"})
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return _login


@pytest.fixture
def grant_access(seeded: Session):
    """Chủ hộ cấp quyền một thiết bị cho thành viên (mô hình quản-lý-theo-thiết-bị).

    Từ khi bỏ cấp-quyền-theo-phòng, thành viên trắng quyền tới khi được cấp; nhiều
    test cần cấp trước một thiết bị để có gì đó mà điều khiển.
    """
    from sqlalchemy import select

    from src.domain.enums import AccessEffect
    from src.domain.models import AccessRule, Device, User

    def _grant(username: str, device_slug: str, effect: AccessEffect = AccessEffect.ACCEPTED) -> None:
        user = seeded.scalar(select(User).where(User.username == username))
        device = seeded.scalar(select(Device).where(Device.slug == device_slug))
        # Upsert: seed đã cấp sẵn quyền phòng riêng nên cặp (user, device) có thể đã
        # tồn tại — cập nhật effect thay vì insert trùng (unique user_id+device_id).
        existing = seeded.scalar(
            select(AccessRule).where(AccessRule.user_id == user.id, AccessRule.device_id == device.id)
        )
        if existing is not None:
            existing.effect = effect
        else:
            seeded.add(
                AccessRule(household_id=user.household_id, user_id=user.id, device_id=device.id, effect=effect)
            )
        seeded.commit()

    return _grant
