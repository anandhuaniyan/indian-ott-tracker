"""Stable discovery role names mapped from provider-supplied credit labels."""

ROLE_ALIASES = {
    "actor": ("cast",),
    "director": ("director",),
    "writer": ("writer", "screenplay", "story"),
    "cinematography": ("director of photography", "cinematography", "cinematographer"),
    "producer": ("producer", "executive producer", "co-producer"),
    "editor": ("editor",),
    "composer": ("original music composer", "composer", "music director", "music"),
}


def normalize_role(value: str | None) -> str | None:
    """Normalize for filters and grouping without changing the original stored value."""
    if not value:
        return None
    needle = value.strip().lower().replace("_", " ").replace("-", " ")
    for role, aliases in ROLE_ALIASES.items():
        if needle == role or needle in aliases:
            return role
    return needle
