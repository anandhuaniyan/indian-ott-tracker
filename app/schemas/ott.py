from pydantic import BaseModel, ConfigDict


class OTTPlatformRead(BaseModel):

    model_config = ConfigDict(
        from_attributes=True
    )

    name: str
    slug: str
    website_url: str | None = None


class MovieOTTRead(BaseModel):

    model_config = ConfigDict(
        from_attributes=True
    )

    watch_url: str | None = None
    region: str
    availability_type: str

    platform: OTTPlatformRead