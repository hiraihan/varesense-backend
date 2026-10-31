import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from varesense.audit.models import AuditLog


def _jsonable(value):
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


async def record_audit(
    session: AsyncSession,
    *,
    actor_user_id: uuid.UUID | None,
    action: str,
    object_type: str,
    object_id: uuid.UUID | None = None,
    details: dict | None = None,
) -> None:
    """Queue an audit row in the caller's transaction; the caller commits.

    Because the row shares the caller's transaction, a failed audit write rolls
    back the sensitive action too — never a success without its audit trail.
    """
    session.add(
        AuditLog(
            actor_user_id=actor_user_id,
            action=action,
            object_type=object_type,
            object_id=object_id,
            details=_jsonable(details) if details else None,
        )
    )
