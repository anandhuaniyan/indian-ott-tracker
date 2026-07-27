"""Shared Pydantic schema utilities."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TimestampSchema(BaseModel):
    """Read-only timestamp fields shared across response schemas."""

    model_config = ConfigDict(from_attributes=True)

    created_at: datetime
    updated_at: datetime
