from app.core.secrets import sanitize_error


def test_sanitize_error_redacts_provider_query_and_authorization_credentials():
    value = (
        "GET https://provider.test/movie/7?append=x&api_key=top-secret "
        "Authorization: Bearer second-secret"
    )

    cleaned = sanitize_error(value)

    assert "top-secret" not in cleaned
    assert "second-secret" not in cleaned
    assert "api_key=[redacted]" in cleaned
    assert "Authorization: Bearer [redacted]" in cleaned


def test_sanitize_error_bounds_persisted_diagnostics():
    assert len(sanitize_error("x" * 5000, limit=120)) == 120
