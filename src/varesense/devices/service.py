import secrets
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from varesense.core.security import hash_password
from varesense.devices.models import DeviceCredential, EdgeNode


async def get_owned_node(
    session: AsyncSession, organization_id: uuid.UUID, node_id: uuid.UUID
) -> EdgeNode:
    node = await session.get(EdgeNode, node_id)
    if node is None or node.organization_id != organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "device not found")
    return node


LIFECYCLE_TRANSITIONS = {
    "provisioning": {"active"},
    "active": {"retired"},
    "retired": set(),
}


def validate_lifecycle_transition(current: str, new: str) -> None:
    if new not in LIFECYCLE_TRANSITIONS[current]:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"cannot transition {current} -> {new}",
        )


async def get_owned_credential(
    session: AsyncSession,
    organization_id: uuid.UUID,
    node_id: uuid.UUID,
    credential_id: uuid.UUID,
) -> DeviceCredential:
    cred = await session.get(DeviceCredential, credential_id)
    if (
        cred is None
        or cred.organization_id != organization_id
        or cred.edge_node_id != node_id
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "credential not found")
    return cred


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def issue_credential_row(node: EdgeNode, organization_id: uuid.UUID, expires_at) -> tuple[DeviceCredential, str]:
    secret = new_secret()
    client_id = f"{node.code or 'node'}-{uuid.uuid4().hex[:8]}"
    credential = DeviceCredential(
        edge_node_id=node.id,
        organization_id=organization_id,
        client_id=client_id,
        key_hash=hash_password(secret),
        expires_at=expires_at,
    )
    return credential, secret
