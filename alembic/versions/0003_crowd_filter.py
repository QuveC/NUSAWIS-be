"""home: index untuk filter tingkat keramaian

Filter keramaian mencari crowd_status dengan level tertentu yang masih baru
(level IN (...) AND updated_at >= batas waktu).

Hanya berlaku untuk PostgreSQL (dilewati di SQLite).

Revision ID: 0003_crowd_filter
Revises: 0002_destination_filters
Create Date: 2026-11-24
"""

from alembic import op

revision = "0003_crowd_filter"
down_revision = "0002_destination_filters"
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_crowd_status_level ON crowd_status (level, updated_at)"
    )


def downgrade() -> None:
    if not _is_postgres():
        return
    op.execute("DROP INDEX IF EXISTS ix_crowd_status_level")
