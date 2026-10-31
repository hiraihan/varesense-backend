from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

RLS_GUC = "app.organization_id"


async def set_org_context(session: AsyncSession, organization_id: str | None) -> None:
    """Bind the transaction to a tenant before any query.

    Requires Postgres policies (ADR-0002) reading current_setting('app.organization_id').
    The value is only visible within this transaction (SET LOCAL semantics).
    """
    value = organization_id or ""
    await session.execute(
        text("SELECT set_config(:guc, :value, true)").bindparams(
            guc=RLS_GUC, value=value
        )
    )
