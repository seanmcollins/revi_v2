"""Alembic environment for the application-state Postgres.

URL resolution: an explicit ``sqlalchemy.url`` main option (set
programmatically by tests) wins, else ``REVI_DATABASE_URL``, else the
docker-compose default. Migrations create the capability-named schemas
themselves (``CREATE SCHEMA IF NOT EXISTS``), so a fresh database needs no
manual setup.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import context

from revi_store_postgres.engine import database_url
from revi_store_postgres.tables import metadata

config = context.config

url = config.get_main_option("sqlalchemy.url") or database_url()

target_metadata = metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live connection."""
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection."""
    engine = sa.create_engine(url, poolclass=sa.pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
