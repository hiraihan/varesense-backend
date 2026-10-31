from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from varesense.core.db import get_session
from varesense.core.rls import set_org_context
from varesense.core.security import verify_password
from varesense.devices.models import DeviceCredential, EdgeNode

basic = HTTPBasic()


async def get_current_device(
    credentials: HTTPBasicCredentials | None = Depends(basic),
    session: AsyncSession = Depends(get_session),
) -> EdgeNode:
    """Authenticate an EdgeNode via its per-device credential (ticket 05)."""
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing credentials")
    cred = await session.scalar(
        select(DeviceCredential).where(
            DeviceCredential.client_id == credentials.username
        )
    )
    if cred is None or cred.revoked_at is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    if cred.expires_at is not None and cred.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    if not verify_password(credentials.password, cred.key_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    node = await session.get(EdgeNode, cred.edge_node_id)
    if node is None or node.lifecycle_state == "retired":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "device not active")
    await set_org_context(session, str(node.organization_id))
    return node
