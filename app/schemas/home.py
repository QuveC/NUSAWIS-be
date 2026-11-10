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


class HomeOut(BaseModel):
    banners: list[BannerOut]
    featured_destinations: list[DestinationOut]
