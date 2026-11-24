import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import settings
from app.models.home import CrowdLevel, DataStatus


class BannerBase(BaseModel):
    title: str = Field(min_length=1, max_length=150)
    image_url: str = Field(min_length=1, max_length=500)
    link_url: str | None = Field(default=None, max_length=500)
    is_active: bool = True
    sort_order: int = 0


class BannerCreate(BannerBase):
    pass


class BannerOut(BannerBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime | None


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str


class DistrictOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class CrowdInfo(BaseModel):
    level: CrowdLevel | None = Field(
        description="SEPI / SEDANG / RAMAI. null = belum ada data atau data sudah kedaluwarsa"
    )
    updated_at: datetime | None = Field(description="Waktu terakhir data keramaian diperbarui")


class CrowdDetail(CrowdInfo):
    current_visitors: int | None = Field(description="Jumlah pengunjung saat ini (null jika tidak diketahui)")


def crowd_snapshot(crowd: Any) -> Any:
    """Ubah baris CrowdStatus (atau None) jadi data publik.

    Data yang lebih lama dari CROWD_STALE_HOURS tidak ditampilkan levelnya, supaya data
    kemarin tidak terbaca sebagai keadaan sekarang. Dict (dari cache) diteruskan apa adanya.
    """
    if isinstance(crowd, dict):
        return crowd
    if crowd is None:
        return {"level": None, "updated_at": None, "current_visitors": None}
    fresh = crowd.is_fresh(settings.CROWD_STALE_HOURS)
    return {
        "level": crowd.level if fresh else None,
        "updated_at": crowd.updated_at,
        "current_visitors": crowd.current_visitors if fresh else None,
    }


class PhotoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    url: str
    caption: str | None


class DestinationBase(BaseModel):
    category_id: int
    district_id: int
    name: str = Field(min_length=1, max_length=150)
    slug: str = Field(min_length=1, max_length=170, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: str | None = None
    address: str = Field(min_length=1)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    contact: str | None = Field(default=None, max_length=30)
    open_hours: dict | None = None
    price_min: int | None = Field(default=None, ge=0)
    price_max: int | None = Field(default=None, ge=0)
    facilities: list[str] | None = None
    attributes: dict | None = None
    capacity: int | None = Field(default=None, gt=0)


class DestinationCreate(DestinationBase):
    status: DataStatus = DataStatus.DRAFT


class DestinationOut(DestinationBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: DataStatus
    avg_rating: float | None
    rating_count: int | None
    category: CategoryOut
    district: DistrictOut
    crowd: CrowdDetail
    photos: list[PhotoOut] = Field(description="Galeri foto, urut sort_order (pertama = sampul)")
    created_at: datetime
    updated_at: datetime

    _crowd = field_validator("crowd", mode="before")(crowd_snapshot)


class CategoryWithCount(CategoryOut):
    destination_count: int


class DistrictWithCount(DistrictOut):
    destination_count: int


class DestinationCard(BaseModel):
    """Versi ringkas destinasi untuk daftar/kartu di home & hasil pencarian.

    Sengaja tanpa description, open_hours, facilities, dll. supaya payload kecil.
    Data lengkap diambil lewat endpoint detail.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    address: str
    category: CategoryOut
    district: DistrictOut
    avg_rating: float | None
    rating_count: int | None
    price_min: int | None
    price_max: int | None
    cover_photo_url: str | None = Field(description="Foto sampul. null = belum ada foto")
    crowd: CrowdInfo

    _crowd = field_validator("crowd", mode="before")(crowd_snapshot)


class DestinationPage(BaseModel):
    items: list[DestinationCard]
    total: int | None = Field(
        description="Jumlah seluruh hasil. Hanya dihitung di halaman pertama (tanpa cursor)."
    )
    limit: int
    has_more: bool
    next_cursor: str | None = Field(
        description="Kirim sebagai ?cursor= untuk halaman berikutnya (khusus user login)."
    )
    locked: bool = Field(
        default=False, description="True = tamu, hasil dipotong. Login untuk melihat semua."
    )
    message: str | None = None


class FacilityOption(BaseModel):
    name: str = Field(description="Kirim persis seperti ini di ?facilities=")
    destination_count: int


class PriceRange(BaseModel):
    min: int | None = Field(description="Harga termurah (Rp), null jika belum ada data harga")
    max: int | None


class FilterOptions(BaseModel):
    categories: list[CategoryWithCount]
    districts: list[DistrictWithCount]
    facilities: list[FacilityOption]
    price: PriceRange
    crowd_levels: list[CrowdLevel]


class HomeOut(BaseModel):
    is_authenticated: bool
    banners: list[BannerOut]
    categories: list[CategoryWithCount]
    districts: list[DistrictWithCount]
    total_destinations: int
    featured_destinations: list[DestinationCard]
    destinations: DestinationPage
