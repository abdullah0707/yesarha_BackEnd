from typing import Optional
from fastapi import Query
from sqlalchemy.orm import Query as SAQuery
from sqlalchemy import asc, desc


class ListParams:
    """
    Shared query params for all list endpoints:
    ?page=1&limit=20&sort=-created_at&search=...
    """

    def __init__(
        self,
        page: int = Query(1, ge=1),
        limit: int = Query(20, ge=1, le=200),
        sort: Optional[str] = Query(None, description="Field name, prefix with '-' for descending"),
        search: Optional[str] = Query(None),
    ):
        self.page = page
        self.limit = limit
        self.sort = sort
        self.search = search

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.limit


def apply_sort(query: SAQuery, model, sort: Optional[str], default_field: str = "id"):
    field_name = default_field
    direction = desc

    if sort:
        if sort.startswith("-"):
            field_name = sort[1:]
            direction = desc
        else:
            field_name = sort
            direction = asc

    column = getattr(model, field_name, None)
    if column is None:
        column = getattr(model, default_field)
        direction = desc

    return query.order_by(direction(column))


def apply_pagination(query: SAQuery, params: ListParams):
    total = query.count()
    items = query.offset(params.offset).limit(params.limit).all()
    return items, total
