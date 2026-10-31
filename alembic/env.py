import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from varesense.alerts import models as alerts_models  # noqa: F401  register tables
from varesense.analytics import models as analytics_models  # noqa: F401  register tables
from varesense.assets import models as assets_models  # noqa: F401  register tables
from varesense.audit import models as audit_models  # noqa: F401  register tables
from varesense.auth import models as auth_models  # noqa: F401  register tables
from varesense.devices import models as devices_models  # noqa: F401  register tables
from varesense.energy import models as energy_models  # noqa: F401  register tables
from varesense.maintenance import models as maintenance_models  # noqa: F401  register tables
from varesense.telemetry import models as telemetry_models  # noqa: F401  register tables
from varesense.core.config import settings
from varesense.core.db import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
