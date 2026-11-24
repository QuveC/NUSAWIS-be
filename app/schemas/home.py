import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.home import DataStatus


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
    created_at: datetime
    updated_at: datetime


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


class HomeOut(BaseModel):
    is_authenticated: bool
    banners: list[BannerOut]
    categories: list[CategoryWithCount]
    districts: list[DistrictWithCount]
    total_destinations: int
    featured_destinations: list[DestinationCard]
    destinations: DestinationPage
