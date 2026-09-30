"""Canonical movie-language configuration and normalization helpers.

``Movie.original_language`` is the sole catalogue-classification field. The
many-to-many ``Movie.languages`` relationship contains spoken/audio languages
and must never be used to place a movie on a language page.

TMDB normally supplies ISO 639-1 codes. A small set of unambiguous ISO 639-2
aliases is accepted for defensive ingestion. ``cn`` is a country code, while
``yue`` (Cantonese) and ``cmn`` (Mandarin) are distinct language codes; none
is silently folded into TMDB's ``zh`` Chinese classification.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class LanguageDefinition:
    code: str
    display_name: str
    slug: str
    group: str
    enabled: bool = True
    tier: str = "primary"

    def public_dict(self) -> dict:
        return asdict(self)


# One source of truth for ingestion, APIs, admin metrics, sitemap generation,
# and the public frontend. Secondary entries preserve established browse routes
# without diluting the primary language controls requested by the site.
LANGUAGE_CONFIG = (
    LanguageDefinition("ml", "Malayalam", "malayalam", "indian"),
    LanguageDefinition("ta", "Tamil", "tamil", "indian"),
    LanguageDefinition("te", "Telugu", "telugu", "indian"),
    LanguageDefinition("hi", "Hindi", "hindi", "indian"),
    LanguageDefinition("kn", "Kannada", "kannada", "indian"),
    LanguageDefinition("bn", "Bengali", "bengali", "indian", tier="more"),
    LanguageDefinition("mr", "Marathi", "marathi", "indian", tier="more"),
    LanguageDefinition("pa", "Punjabi", "punjabi", "indian", tier="more"),
    LanguageDefinition("gu", "Gujarati", "gujarati", "indian", tier="more"),
    LanguageDefinition("or", "Odia", "odia", "indian", tier="more"),
    LanguageDefinition("as", "Assamese", "assamese", "indian", tier="more"),
    LanguageDefinition("ur", "Urdu", "urdu", "indian", enabled=False, tier="more"),
    LanguageDefinition("sa", "Sanskrit", "sanskrit", "indian", enabled=False, tier="more"),
    LanguageDefinition("ne", "Nepali", "nepali", "indian", enabled=False, tier="more"),
    LanguageDefinition("si", "Sinhala", "sinhala", "indian", enabled=False, tier="more"),
    LanguageDefinition("sd", "Sindhi", "sindhi", "indian", enabled=False, tier="more"),
    LanguageDefinition("ks", "Kashmiri", "kashmiri", "indian", enabled=False, tier="more"),
    LanguageDefinition("en", "English", "english", "international"),
    LanguageDefinition("ko", "Korean", "korean", "international"),
    LanguageDefinition("ja", "Japanese", "japanese", "international"),
    LanguageDefinition("zh", "Chinese", "chinese", "international"),
    LanguageDefinition("es", "Spanish", "spanish", "international"),
    LanguageDefinition("fr", "French", "french", "international"),
    LanguageDefinition("th", "Thai", "thai", "international", tier="more"),
)

LANGUAGE_BY_CODE = {item.code: item for item in LANGUAGE_CONFIG}

# Only aliases whose meaning is unambiguous are normalized. In particular,
# cn/yue/cmn deliberately remain distinct and unsupported unless the product
# later adds explicit language definitions for them.
LANGUAGE_CODE_ALIASES = {
    "eng": "en",
    "jpn": "ja",
    "kor": "ko",
    "fra": "fr",
    "fre": "fr",
    "spa": "es",
}


def normalize_language_code(code: str | None) -> str | None:
    """Return a stable lowercase code without broad language assumptions."""
    if code is None:
        return None
    normalized = str(code).strip().lower()
    if not normalized:
        return None
    return LANGUAGE_CODE_ALIASES.get(normalized, normalized)


def language_definition(code: str | None) -> LanguageDefinition | None:
    normalized = normalize_language_code(code)
    return LANGUAGE_BY_CODE.get(normalized) if normalized else None


def _select(*, group: str | None = None, tier: str | None = None):
    return tuple(
        item
        for item in LANGUAGE_CONFIG
        if item.enabled
        and (group is None or item.group == group)
        and (tier is None or item.tier == tier)
    )


INDIAN_PRIMARY_DEFINITIONS = _select(group="indian", tier="primary")
MORE_INDIAN_DEFINITIONS = _select(group="indian", tier="more")
INTERNATIONAL_PRIMARY_DEFINITIONS = _select(
    group="international", tier="primary"
)
MORE_INTERNATIONAL_DEFINITIONS = _select(group="international", tier="more")

# Compatibility projections for existing services. These are derived from
# LANGUAGE_CONFIG rather than maintained as competing lists.
INDIAN_PRIMARY_LANGUAGES = tuple(
    (item.code, item.display_name) for item in INDIAN_PRIMARY_DEFINITIONS
)
MORE_INDIAN_LANGUAGES = tuple(
    (item.code, item.display_name) for item in MORE_INDIAN_DEFINITIONS
)
INTERNATIONAL_PRIMARY_LANGUAGES = tuple(
    (item.code, item.display_name) for item in INTERNATIONAL_PRIMARY_DEFINITIONS
)
MORE_INTERNATIONAL_LANGUAGES = tuple(
    (item.code, item.display_name) for item in MORE_INTERNATIONAL_DEFINITIONS
)
SUPPORTED_PRIMARY_LANGUAGES = (
    *INDIAN_PRIMARY_LANGUAGES,
    *INTERNATIONAL_PRIMARY_LANGUAGES,
)
ALL_PUBLIC_LANGUAGES = tuple(
    (item.code, item.display_name) for item in _select()
)
SUPPORTED_PRIMARY_LANGUAGE_NAMES = dict(SUPPORTED_PRIMARY_LANGUAGES)
SUPPORTED_PRIMARY_LANGUAGE_CODES = tuple(SUPPORTED_PRIMARY_LANGUAGE_NAMES)
ALL_PUBLIC_LANGUAGE_NAMES = dict(ALL_PUBLIC_LANGUAGES)
ALL_PUBLIC_LANGUAGE_CODES = tuple(ALL_PUBLIC_LANGUAGE_NAMES)
INDIAN_PRIMARY_LANGUAGE_CODES = tuple(
    item.code for item in INDIAN_PRIMARY_DEFINITIONS
)
INTERNATIONAL_PRIMARY_LANGUAGE_CODES = tuple(
    item.code for item in INTERNATIONAL_PRIMARY_DEFINITIONS
)
# The global movie-list navigation deliberately groups only the five primary
# Indian languages. Other enabled languages retain their direct browse pages,
# but are not silently included in the product-level INDIAN filter.
GLOBAL_LANGUAGE_FILTER_VALUES = (
    "all",
    "indian",
    *INTERNATIONAL_PRIMARY_LANGUAGE_CODES,
)
INDIAN_LANGUAGE_CODES = tuple(item.code for item in _select(group="indian"))
INTERNATIONAL_LANGUAGE_CODES = tuple(
    item.code for item in _select(group="international")
)
INDIAN_ORIGINAL_LANGUAGE_CODES = tuple(
    item.code for item in LANGUAGE_CONFIG if item.group == "indian"
)


def language_filter_codes(value: str | None) -> tuple[str, ...] | None:
    """Resolve a public movie-list filter to canonical original-language codes.

    ``None`` means no language restriction. The grouped value is a query-only
    concept and must never be persisted as ``Movie.original_language``.
    """
    normalized = normalize_language_code(value)
    if normalized in (None, "all"):
        return None
    if normalized == "indian":
        return INDIAN_PRIMARY_LANGUAGE_CODES
    if normalized in ALL_PUBLIC_LANGUAGE_CODES:
        return (normalized,)
    raise ValueError("Unknown language filter")

LANGUAGE_NAMES = {
    "ar": "Arabic", "as": "Assamese", "bn": "Bengali", "bo": "Tibetan",
    "cmn": "Mandarin Chinese", "cs": "Czech", "da": "Danish",
    "de": "German", "el": "Greek", "en": "English", "es": "Spanish",
    "fa": "Persian", "fi": "Finnish", "fr": "French", "gu": "Gujarati",
    "he": "Hebrew", "hi": "Hindi", "hu": "Hungarian", "id": "Indonesian",
    "it": "Italian", "ja": "Japanese", "ka": "Georgian", "kk": "Kazakh",
    "kn": "Kannada", "ko": "Korean", "ks": "Kashmiri", "lt": "Lithuanian",
    "lv": "Latvian", "ml": "Malayalam", "mn": "Mongolian", "mr": "Marathi",
    "ms": "Malay", "my": "Burmese", "ne": "Nepali", "nl": "Dutch",
    "no": "Norwegian", "or": "Odia", "pa": "Punjabi", "pl": "Polish",
    "ps": "Pashto", "pt": "Portuguese", "ro": "Romanian", "ru": "Russian",
    "sa": "Sanskrit", "sd": "Sindhi", "si": "Sinhala", "sk": "Slovak",
    "sr": "Serbian", "sv": "Swedish", "ta": "Tamil", "te": "Telugu",
    "th": "Thai", "tr": "Turkish", "uk": "Ukrainian", "ur": "Urdu",
    "uz": "Uzbek", "vi": "Vietnamese", "yue": "Cantonese",
    "zh": "Chinese",
}


def public_language_config() -> list[dict]:
    """Serializable enabled configuration for API/frontend consumers."""
    return [item.public_dict() for item in LANGUAGE_CONFIG if item.enabled]


def language_name(code: str | None, stored_name: str | None = None) -> str | None:
    """Return a stable public name for an original-language code."""
    normalized = normalize_language_code(code)
    definition = LANGUAGE_BY_CODE.get(normalized) if normalized else None
    if definition:
        return definition.display_name
    if stored_name and stored_name.strip():
        return stored_name.strip()
    if not normalized:
        return None
    return LANGUAGE_NAMES.get(normalized, normalized)
