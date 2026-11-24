import enum
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    FetchedValue,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base

# JSONB / TEXT[] / TSVECTOR di Postgres, tipe biasa di SQLite (untuk test)
JSONType = JSON().with_variant(JSONB(), "postgresql")
TextArray = JSON().with_variant(ARRAY(Text), "postgresql")
TSVectorType = Text().with_variant(TSVECTOR(), "postgresql")


class DataStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    VERIFIED = "VERIFIED"
    PUBLISHED = "PUBLISHED"


class CrowdLevel(str, enum.Enum):
    SEPI = "SEPI"
    SEDANG = "SEDANG"
    RAMAI = "RAMAI"


class Banner(Base):
    __tablename__ = "banners"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    image_url: Mapped[str] = mapped_column(String(500), nullable=False)
    link_url: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now())


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)


class District(Base):
    __tablename__ = "districts"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)


class CrowdStatus(Base):
    """Tingkat keramaian terkini per destinasi (satu baris per destinasi).

    Belum ada fitur yang mengisi tabel ini (input pengunjung oleh Pengelola menyusul).
    """

    __tablename__ = "crowd_status"
    __table_args__ = (
        CheckConstraint("current_visitors >= 0", name="crowd_status_current_visitors_check"),
        Index("ix_crowd_status_level", "level", "updated_at"),  # migrasi 0003_crowd_filter
    )

    destination_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("destinations.id", ondelete="CASCADE"), primary_key=True
    )
    current_visitors: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    level: Mapped[CrowdLevel] = mapped_column(
        Enum(CrowdLevel, name="crowd_lvl"),
        nullable=False,
        default=CrowdLevel.SEPI,
        server_default=CrowdLevel.SEPI.value,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def is_fresh(self, max_age_hours: float, now: datetime | None = None) -> bool:
        """False kalau data lebih lama dari `max_age_hours` (dianggap tidak diketahui)."""
        updated = self.updated_at
        if updated is None:
            return False
        if updated.tzinfo is None:  # SQLite menyimpan tanpa zona waktu (UTC)
            updated = updated.replace(tzinfo=timezone.utc)
        now = now or datetime.now(timezone.utc)
        return now - updated <= timedelta(hours=max_age_hours)


class Destination(Base):
    __tablename__ = "destinations"
    # Index dibuat di Postgres lewat migrasi alembic (0001_home_search); dideklarasikan juga
    # di sini supaya `alembic revision --autogenerate` tidak mengusulkan menghapusnya.
    __table_args__ = (
        UniqueConstraint("name", "address"),
        Index("ix_destinations_status_category", "status", "category_id"),
        Index("ix_destinations_status_district", "status", "district_id"),
        Index(
            "ix_destinations_published_name",
            "name",
            "id",
            postgresql_where=text("status = 'PUBLISHED'"),
        ),
        Index("ix_destinations_search_vector", "search_vector", postgresql_using="gin"),
        # migrasi 0002_destination_filters
        Index("ix_destinations_facilities", "facilities", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )
    category_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("categories.id"), nullable=False
    )
    district_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("districts.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    slug: Mapped[str] = mapped_column(String(170), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    lat: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    lng: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    contact: Mapped[str | None] = mapped_column(String(30))
    open_hours: Mapped[dict | None] = mapped_column(JSONType)
    price_min: Mapped[int | None] = mapped_column(Integer)
    price_max: Mapped[int | None] = mapped_column(Integer)
    facilities: Mapped[list[str] | None] = mapped_column(TextArray, default=list)
    attributes: Mapped[dict | None] = mapped_column(JSONType, default=dict)
    capacity: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[DataStatus] = mapped_column(
        Enum(DataStatus, name="data_status"),
        nullable=False,
        default=DataStatus.DRAFT,
        server_default=DataStatus.DRAFT.value,
    )
    avg_rating: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), default=0)
    rating_count: Mapped[int | None] = mapped_column(Integer, default=0)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    # Di Postgres ini kolom GENERATED ALWAYS:
    #   to_tsvector('indonesian', coalesce(name,'') || ' ' || coalesce(description,''))
    # Diisi database, tidak pernah ditulis app. FetchedValue = beri tahu SQLAlchemy nilainya
    # dibuat server, jadi kolom ini tidak pernah ikut INSERT/UPDATE (Postgres menolak nilai
    # apa pun, termasuk NULL, untuk kolom generated). deferred = tidak ikut di-SELECT.
    search_vector: Mapped[str | None] = mapped_column(
        TSVectorType,
        server_default=FetchedValue(),
        server_onupdate=FetchedValue(),
        deferred=True,
    )

    category: Mapped[Category] = relationship(lazy="joined")
    district: Mapped[District] = relationship(lazy="joined")
    # Satu baris per destinasi (PK = destination_id), ikut di-JOIN saat destinasi dimuat.
    crowd: Mapped[CrowdStatus | None] = relationship(lazy="joined", uselist=False, viewonly=True)
