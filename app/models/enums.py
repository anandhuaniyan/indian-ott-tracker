"""Shared enumerations for database models."""

import enum

from sqlalchemy import Enum as SAEnum


class AvailabilityType(str, enum.Enum):
    """How content can be watched on an OTT platform."""

    STREAM = "stream"
    FREE = "free"
    RENT = "rent"
    BUY = "buy"


availability_type_enum = SAEnum(
    AvailabilityType,
    name="availability_type",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)
