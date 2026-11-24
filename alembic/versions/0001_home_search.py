"""home: index untuk daftar & pencarian destinasi

- GIN index pada destinations.search_vector -> pencarian full-text tidak scan seluruh tabel.
  (search_vector sendiri sudah kolom GENERATED di database:
   to_tsvector('indonesian', name || ' ' || description), jadi tidak perlu trigger.)
- Index untuk filter kategori/kecamatan dan urutan nama (keyset pagination).

Hanya berlaku untuk PostgreSQL (dilewati di SQLite).

Revision ID: 0001_home_search
Revises:
Create Date: 2026-11-24
"""

from alembic import op

revision = "0001_home_search"
down_revision = None
branch_labels = None
depends_on = None

INDEXES = {
    "ix_destinations_search_vector": "ON destinations USING gin (search_vector)",
    "ix_destinations_status_category": "ON destinations (status, category_id)",
    "ix_destinations_status_district": "ON destinations (status, district_id)",
    "ix_destinations_published_name": "ON destinations (name, id) WHERE status = 'PUBLISHED'",
}


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return
    for name, definition in INDEXES.items():
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} {definition}")


def downgrade() -> None:
    if not _is_postgres():
        return
    for name in INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")
