"""Reusable ISO 639-1 presentation helpers for public APIs."""

LANGUAGE_NAMES = {
    "ar": "Arabic", "as": "Assamese", "bn": "Bengali", "bo": "Tibetan",
    "cs": "Czech", "da": "Danish", "de": "German", "el": "Greek",
    "en": "English", "es": "Spanish", "fa": "Persian", "fi": "Finnish",
    "fr": "French", "gu": "Gujarati", "he": "Hebrew", "hi": "Hindi",
    "hu": "Hungarian", "id": "Indonesian", "it": "Italian", "ja": "Japanese",
    "ka": "Georgian", "kk": "Kazakh", "kn": "Kannada", "ko": "Korean",
    "ks": "Kashmiri", "lt": "Lithuanian", "lv": "Latvian", "ml": "Malayalam",
    "mn": "Mongolian", "mr": "Marathi", "ms": "Malay", "my": "Burmese",
    "ne": "Nepali", "nl": "Dutch", "no": "Norwegian", "or": "Odia",
    "pa": "Punjabi", "pl": "Polish", "ps": "Pashto", "pt": "Portuguese",
    "ro": "Romanian", "ru": "Russian", "sa": "Sanskrit", "sd": "Sindhi",
    "si": "Sinhala", "sk": "Slovak", "sr": "Serbian", "sv": "Swedish",
    "ta": "Tamil", "te": "Telugu", "th": "Thai", "tr": "Turkish",
    "uk": "Ukrainian", "ur": "Urdu", "uz": "Uzbek", "vi": "Vietnamese",
    "zh": "Chinese",
}


def language_name(code: str | None, stored_name: str | None = None) -> str | None:
    """Prefer stored ISO metadata, then a broad common-name map, then the code."""
    if stored_name and stored_name.strip():
        return stored_name.strip()
    if not code:
        return None
    normalized = code.strip().lower()
    return LANGUAGE_NAMES.get(normalized, code)
