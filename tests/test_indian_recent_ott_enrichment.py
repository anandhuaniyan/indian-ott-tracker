from app.services.ott.indian_recent_enrichment import LANGUAGES, OPERATION, YEARS


def test_recent_indian_operation_has_bounded_priority_scope():
    assert OPERATION == "operations.indian_recent_ott_enrichment"
    assert LANGUAGES == ("ml", "ta", "te", "hi", "kn")
    assert YEARS == (2026, 2025, 2024, 2023, 2022, 2021, 2020)
