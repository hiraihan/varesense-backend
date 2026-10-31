import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from varesense.assets.service import get_owned_asset
from varesense.audit.service import record_audit
from varesense.auth.deps import get_current_user
from varesense.auth.models import User
from varesense.core.db import get_session
from varesense.devices.models import DeviceCredential, DeviceEndpoint, EdgeNode
from varesense.realtime.hub import hub
from varesense.devices.schemas import (
    CredentialIssue,
    CredentialOut,
    EdgeNodeCreate,
    EdgeNodeOut,
    EdgeNodeUpdate,
    EndpointCreate,
    EndpointOut,
)
from varesense.devices.service import (
    get_owned_credential,
    get_owned_node,
    issue_credential_row,
    validate_lifecycle_transition,
)

router = APIRouter(prefix="/devices", tags=["devices"])


def _device_status_payload(node: EdgeNode) -> dict:
    return {
        "type": "device_status",
        "device_id": str(node.id),
        "lifecycle_state": node.lifecycle_state,
        "device_status": node.device_status,
        "updated_at": node.updated_at.isoformat() if node.updated_at else None,
    }


@router.post("", response_model=EdgeNodeOut)
async def create_device(
    body: EdgeNodeCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    node = EdgeNode(organization_id=user.organization_id, **body.model_dump())
    session.add(node)
    await session.flush()
    await record_audit(
        session,
        actor_user_id=user.id,
        action="create_device",
        object_type="edge_node",
        object_id=node.id,
        details=body.model_dump(),
    )
    await session.commit()
    await session.refresh(node)
    return node


@router.get("", response_model=list[EdgeNodeOut])
async def list_devices(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(EdgeNode)
        .where(EdgeNode.organization_id == user.organization_id)
        .order_by(EdgeNode.created_at)
    )
    return result.scalars().all()


@router.get("/{node_id}", response_model=EdgeNodeOut)
async def get_device(
    node_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await get_owned_node(session, user.organization_id, node_id)


@router.patch("/{node_id}", response_model=EdgeNodeOut)
async def update_device(
    node_id: uuid.UUID,
    body: EdgeNodeUpdate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    node = await get_owned_node(session, user.organization_id, node_id)
    fields = body.model_fields_set
    changes: list[str] = []
    if "lifecycle_state" in fields and body.lifecycle_state is not None and body.lifecycle_state != node.lifecycle_state:
        validate_lifecycle_transition(node.lifecycle_state, body.lifecycle_state)
        changes.append("lifecycle_state")
    if "name" in fields and body.name is not None and body.name != node.name:
        changes.append("name")
    if "code" in fields and body.code != node.code:
        changes.append("code")
    if "device_status" in fields and body.device_status is not None and body.device_status != node.device_status:
        changes.append("device_status")
    if "data" in fields and body.data != node.data:
        changes.append("data")
    if not changes:
        return node
    if "name" in changes and body.name is not None:
        node.name = body.name
    if "code" in changes:
        node.code = body.code
    if "lifecycle_state" in changes and body.lifecycle_state is not None:
        node.lifecycle_state = body.lifecycle_state
    if "device_status" in changes and body.device_status is not None:
        node.device_status = body.device_status
    if "data" in changes:
        node.data = body.data
    await record_audit(
        session,
        actor_user_id=user.id,
        action="update_device",
        object_type="edge_node",
        object_id=node.id,
        details={"changed": changes},
    )
    await session.commit()
    await session.refresh(node)
    if "lifecycle_state" in changes or "device_status" in changes:
        hub.publish(node.organization_id, _device_status_payload(node))
    return node


def _credential_out(cred: DeviceCredential, secret: str | None = None) -> CredentialOut:
    return CredentialOut(
        id=cred.id,
        edge_node_id=cred.edge_node_id,
        client_id=cred.client_id,
        secret=secret,
        issued_at=cred.issued_at,
        expires_at=cred.expires_at,
        revoked_at=cred.revoked_at,
    )


@router.post("/{node_id}/credentials", response_model=CredentialOut)
async def issue_credential(
    node_id: uuid.UUID,
    body: CredentialIssue,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    node = await get_owned_node(session, user.organization_id, node_id)
    if node.lifecycle_state == "retired":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "device is retired")
    credential, secret = issue_credential_row(node, user.organization_id, body.expires_at)
    session.add(credential)
    await session.flush()
    await record_audit(
        session,
        actor_user_id=user.id,
        action="issue_device_credential",
        object_type="device_credential",
        object_id=credential.id,
        details={"edge_node_id": str(node.id)},
    )
    await session.commit()
    await session.refresh(credential)
    return _credential_out(credential, secret)


@router.post("/{node_id}/credentials/{credential_id}/rotate", response_model=CredentialOut)
async def rotate_credential(
    node_id: uuid.UUID,
    credential_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    node = await get_owned_node(session, user.organization_id, node_id)
    if node.lifecycle_state == "retired":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "device is retired")
    old = await get_owned_credential(session, user.organization_id, node_id, credential_id)
    if old.revoked_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "credential already revoked")
    old.revoked_at = datetime.now(timezone.utc)
    # Carry the previous credential's expiry forward so rotation never silently
    # upgrades an expiring credential to a permanent one.
    credential, secret = issue_credential_row(node, user.organization_id, old.expires_at)
    session.add(credential)
    await session.flush()
    await record_audit(
        session,
        actor_user_id=user.id,
        action="rotate_device_credential",
        object_type="device_credential",
        object_id=credential.id,
        details={"edge_node_id": str(node.id), "previous_credential_id": str(old.id)},
    )
    await session.commit()
    await session.refresh(credential)
    return _credential_out(credential, secret)


@router.post("/{node_id}/credentials/{credential_id}/revoke", response_model=CredentialOut)
async def revoke_credential(
    node_id: uuid.UUID,
    credential_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await get_owned_node(session, user.organization_id, node_id)
    credential = await get_owned_credential(session, user.organization_id, node_id, credential_id)
    if credential.revoked_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "credential already revoked")
    credential.revoked_at = datetime.now(timezone.utc)
    await record_audit(
        session,
        actor_user_id=user.id,
        action="revoke_device_credential",
        object_type="device_credential",
        object_id=credential.id,
        details={"edge_node_id": str(node_id)},
    )
    await session.commit()
    await session.refresh(credential)
    return _credential_out(credential)


@router.post("/{node_id}/endpoints", response_model=EndpointOut)
async def create_endpoint(
    node_id: uuid.UUID,
    body: EndpointCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    node = await get_owned_node(session, user.organization_id, node_id)
    asset = await get_owned_asset(session, user.organization_id, body.asset_id)
    if asset.type not in ("machine", "component"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "endpoint must map to a machine or component asset",
        )
    endpoint = DeviceEndpoint(
        organization_id=user.organization_id,
        edge_node_id=node.id,
        **body.model_dump(),
    )
    session.add(endpoint)
    await session.flush()
    await record_audit(
        session,
        actor_user_id=user.id,
        action="map_device_endpoint",
        object_type="device_endpoint",
        object_id=endpoint.id,
        details={"edge_node_id": str(node.id), "asset_id": str(body.asset_id), "kind": body.kind},
    )
    await session.commit()
    await session.refresh(endpoint)
    return endpoint


@router.get("/{node_id}/endpoints", response_model=list[EndpointOut])
async def list_endpoints(
    node_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await get_owned_node(session, user.organization_id, node_id)
    result = await session.execute(
        select(DeviceEndpoint)
        .where(
            DeviceEndpoint.edge_node_id == node_id,
            DeviceEndpoint.organization_id == user.organization_id,
        )
        .order_by(DeviceEndpoint.created_at)
    )
    return result.scalars().all()
