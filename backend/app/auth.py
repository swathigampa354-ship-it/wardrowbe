"""Auth for the trial.

Default posture is *no authentication*: one implicit demo user. That is a
deliberate trial decision (spec section 14), not a production pattern.

Two optional gates exist:
  * ``REQUIRE_AUTH=true``  -> every request needs a Bearer token issued by
    ``POST /api/v1/auth/sync`` (the shape the existing Next.js frontend already
    speaks, so no frontend rewrite was needed for it).
  * ``DEMO_PASSWORD=...``  -> that sync call also requires the shared password,
    so a public URL is not wide open.
"""

import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import jwt
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import User

logger = logging.getLogger(__name__)
def _settings():
    """Accessed, not snapshotted: a module-level copy would freeze the env at import."""
    return get_settings()

DEMO_USER_ID = "00000000-0000-0000-0000-000000000001"
DEMO_USER_EMAIL = "demo@wardrowbe.local"
TOKEN_TTL_DAYS = 30


def issue_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "email": DEMO_USER_EMAIL,
        "exp": datetime.now(UTC) + timedelta(days=TOKEN_TTL_DAYS),
    }
    return jwt.encode(payload, _settings().secret_key, algorithm="HS256")


_issue_token = issue_token  # back-compat alias for the internal call sites


def _decode_token(token: str) -> str | None:
    try:
        payload: dict[str, Any] = jwt.decode(token, _settings().secret_key, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        logger.debug("Rejected token: %s", exc)
        return None
    sub = payload.get("sub")
    return str(sub) if isinstance(sub, str) else None


def check_demo_password(candidate: str | None) -> None:
    """No-op when DEMO_PASSWORD is unset; constant-time compare otherwise."""
    if not _settings().demo_password:
        return
    if not candidate or not secrets.compare_digest(candidate, _settings().demo_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This demo is password protected. Provide the demo password at /login.",
        )


async def _get_or_create_demo_user(db: AsyncSession) -> User:
    user = await db.get(User, DEMO_USER_ID)
    if user is None:
        user = User(
            id=DEMO_USER_ID,
            email=DEMO_USER_EMAIL,
            display_name="Demo User",
            default_occasion="casual",
        )
        db.add(user)
        # Commit, do not merely flush: a flushed-but-uncommitted INSERT leaves
        # the connection holding SQLite's single write slot for the rest of the
        # request, which starves every other writer (bulk uploads, background
        # analyses) with "database is locked". The demo row is idempotent and
        # must exist before anything references it.
        await db.commit()
    return user


async def get_current_user(
    db: Annotated[AsyncSession, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    if not _settings().require_auth:
        return await _get_or_create_demo_user(db)

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated", headers={}
        )
    user_id = _decode_token(authorization.split(" ", 1)[1].strip())
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    user = await db.get(User, user_id)
    if user is None:
        user = await _get_or_create_demo_user(db)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
