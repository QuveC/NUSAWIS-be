"""home: GIN index untuk filter fasilitas destinasi

Filter `facilities @> ARRAY[...]` (wajib punya semua fasilitas) tidak bisa memakai B-tree;
GIN index membuatnya tidak perlu scan seluruh tabel.

Hanya berlaku untuk PostgreSQL (dilewati di SQLite).

Revision ID: 0002_destination_filters
Revises: 0001_home_search
Create Date: 2026-11-24
"""

from alembic import op

revision = "0002_destination_filters"
down_revision = "0001_home_search"
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_destinations_facilities "
        "ON destinations USING gin (facilities)"
    )


def downgrade() -> None:
    if not _is_postgres():
        return
    op.execute("DROP INDEX IF EXISTS ix_destinations_facilities")
