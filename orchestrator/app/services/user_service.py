"""Per-user provisioning: connector rows, legacy adoption, profile helpers."""

import logging

from sqlmodel import select

from ..connector_catalog import CATALOG, DEFAULT_ENABLED
from ..db import read_session, write_session
from ..models import Connector, User

log = logging.getLogger(__name__)

# A pleasant rotation of accent colors for new profiles.
AVATAR_COLORS = ["#f59e0b", "#22c55e", "#3b82f6", "#ec4899", "#8b5cf6", "#14b8a6", "#ef4444"]


def on_user_created(user_id: int, adopt_legacy: bool = False) -> None:
    """Seed the new profile's connector catalog. The FIRST user additionally
    adopts pre-multi-user global rows (user_id NULL) so an upgraded install
    keeps its configured tokens."""
    with write_session() as db:
        if adopt_legacy:
            legacy = db.exec(
                select(Connector).where(Connector.user_id == None)  # noqa: E711
            ).all()
            for row in legacy:
                row.user_id = user_id
                db.add(row)
            if legacy:
                log.info("adopted %d legacy connector rows for first user", len(legacy))

        existing = {
            c.kind
            for c in db.exec(
                select(Connector).where(Connector.user_id == user_id)
            ).all()
        }
        for kind in CATALOG:
            if kind not in existing:
                db.add(
                    Connector(
                        user_id=user_id,
                        kind=kind,
                        enabled=DEFAULT_ENABLED.get(kind, False),
                    )
                )

        user = db.get(User, user_id)
        if user and not user.avatar_color:
            user.avatar_color = AVATAR_COLORS[(user_id - 1) % len(AVATAR_COLORS)]
            db.add(user)


def user_connectors(user_id: int | None) -> list[Connector]:
    with read_session() as db:
        return list(
            db.exec(select(Connector).where(Connector.user_id == user_id)).all()
        )


def public_profile(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "is_admin": user.is_admin,
        "avatar_color": user.avatar_color,
        "memory_enabled": user.memory_enabled,
        "personal_instructions": user.personal_instructions,
        "created_at": user.created_at.isoformat(),
    }
