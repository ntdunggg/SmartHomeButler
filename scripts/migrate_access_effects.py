"""Chuyển giá trị ``AccessRule.effect`` cũ sang mô hình mới (chạy MỘT LẦN).

Trước:  blocked | approval | allowed
Sau:    (xoá)   | request  | accepted

- ``allowed``  → ``accepted``  (cho dùng ngay)
- ``approval`` → ``request``   (phải xin, chủ hộ duyệt)
- ``blocked``  → XOÁ luật      (không có luật = chưa cấp, thiết bị bị ẩn)

DB mới (chưa từng cấp quyền riêng) không có bản ghi nào nên không cần chạy. Chạy:

    python -m scripts.migrate_access_effects
"""

from __future__ import annotations

from sqlalchemy import delete, update

from src.db.session import session_scope
from src.domain.models import AccessRule


def migrate() -> dict[str, int]:
    with session_scope() as session:
        deleted = session.execute(delete(AccessRule).where(AccessRule.effect == "blocked")).rowcount or 0
        to_accepted = (
            session.execute(update(AccessRule).where(AccessRule.effect == "allowed").values(effect="accepted")).rowcount
            or 0
        )
        to_request = (
            session.execute(update(AccessRule).where(AccessRule.effect == "approval").values(effect="request")).rowcount
            or 0
        )
        session.commit()
    return {"blocked_deleted": deleted, "allowed_to_accepted": to_accepted, "approval_to_request": to_request}


if __name__ == "__main__":
    result = migrate()
    print(
        "Migrate xong: "
        f"xoá {result['blocked_deleted']} luật 'blocked', "
        f"đổi {result['allowed_to_accepted']} 'allowed'→'accepted', "
        f"{result['approval_to_request']} 'approval'→'request'."
    )
