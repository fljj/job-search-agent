from pydantic import BaseModel, Field


class Meta(BaseModel):
    request_id: str


class ApiResponse[T](BaseModel):
    data: T
    meta: Meta


class Page[T](BaseModel):
    items: list[T]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total: int = Field(ge=0)
