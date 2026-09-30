from datetime import date

import httpx
import pytest

from app.models.movie import Movie
from app.models.operations import OttEvidence
from app.models.ott_availability import OttAvailability
from app.services.ott.ai_research import (
    AIProviderResult,
    AIResearchProviderError,
    FallbackAIResearchProvider,
    MovieResearchIdentity,
    OmniRouteResearchProvider,
    OTTResearchAgent,
    ProviderErrorCode,
    ProviderFinding,
    ResearchDecision,
    StructuredWebResearchProvider,
)


class FakeProvider:
    name = "test-ai"
    model = "web-model"
    configured = True

    def __init__(self, findings=(), conflicts=()):
        self.findings = list(findings)
        self.conflicts = list(conflicts)
        self.identity = None

    def research(self, identity, queries):
        self.identity = identity
        return AIProviderResult(self.name, self.model, self.findings, queries, self.conflicts)


def finding(**overrides):
    values = {
        "source_url": "https://www.netflix.com/in/title/example",
        "source_title": "Example Film",
        "platform": "Netflix",
        "release_date": "2026-09-18",
        "region": "IN",
        "watch_type": "SUBSCRIPTION",
        "evidence_summary": "Example Film is streaming on Netflix India.",
        "identity_confidence": 98,
        "fact_confidence": 96,
        "query": '"Example Film" OTT release date',
    }
    values.update(overrides)
    return ProviderFinding(**values)


def inspected(**overrides):
    values = {
        "url": "https://www.netflix.com/in/title/example",
        "title": "Example Film",
        "source_name": "netflix.com",
        "source_type": "official_platform",
        "country": "IN",
        "platform": "Netflix",
        "release_date": "2026-09-18",
        "confidence": 95,
        "evidence_summary": "Example Film is available on Netflix India by subscription from September 18, 2026.",
    }
    values.update(overrides)
    return values


def omniroute_provider():
    return OmniRouteResearchProvider(
        provider="omniroute",
        model="groq/openai/gpt-oss-20b",
        api_url="http://omniroute.test/v1/research",
        api_key="secret",
    )


def omniroute_identity():
    return MovieResearchIdentity(1, 101, "tt0000101", "Example Film", "Example Film", 2026, "ml", ("IN",))


def omniroute_success_body():
    answer = {
        "findings": [{
            "source_url": "https://www.netflix.com/in/title/example",
            "platform": "Netflix",
            "release_date": "2026-09-18",
            "region": "IN",
            "watch_type": "SUBSCRIPTION",
            "evidence_summary": "Example Film streams on Netflix India from September 18, 2026.",
            "identity_confidence": 98,
            "platform_confidence": 97,
            "date_confidence": 96,
            "query": "Example Film tt0000101 OTT India",
        }],
        "conflicts": [],
    }
    return {
        "mode": "WEB_RESEARCH",
        "search_executed": True,
        "source_content_retrieved": True,
        "answer": __import__("json").dumps(answer),
        "sources": [{
            "url": "https://www.netflix.com/in/title/example",
            "title": "Example Film",
            "domain": "netflix.com",
        }],
        "provider": "groq",
        "model": "groq/openai/gpt-oss-20b",
    }


def test_omniroute_successful_research_response(monkeypatch):
    request = httpx.Request("POST", "http://omniroute.test/v1/research")
    monkeypatch.setattr(httpx, "post", lambda *_, **__: httpx.Response(200, request=request, json=omniroute_success_body()))
    result = omniroute_provider().research(omniroute_identity(), ["identity-aware query"])
    assert result.provider == "groq"
    assert result.model == "groq/openai/gpt-oss-20b"
    assert len(result.findings) == 1
    assert result.findings[0].source_url == "https://www.netflix.com/in/title/example"
    assert result.findings[0].platform_confidence == 97
    assert result.findings[0].date_confidence == 96


@pytest.mark.parametrize(
    "gateway_code,expected",
    [
        ("AUTHENTICATION_FAILED", ProviderErrorCode.AUTHENTICATION_FAILED),
        ("QUOTA_EXCEEDED", ProviderErrorCode.QUOTA_EXCEEDED),
        ("RATE_LIMITED", ProviderErrorCode.RATE_LIMITED),
        ("PAYMENT_REQUIRED", ProviderErrorCode.PAYMENT_REQUIRED),
        ("TIMEOUT", ProviderErrorCode.TIMEOUT),
        ("PROVIDER_UNAVAILABLE", ProviderErrorCode.UNAVAILABLE),
        ("MODEL_UNAVAILABLE", ProviderErrorCode.MODEL_UNAVAILABLE),
        ("SEARCH_UNAVAILABLE", ProviderErrorCode.SEARCH_UNAVAILABLE),
        ("MALFORMED_RESPONSE", ProviderErrorCode.MALFORMED_RESPONSE),
    ],
)
def test_omniroute_typed_failures(monkeypatch, gateway_code, expected):
    request = httpx.Request("POST", "http://omniroute.test/v1/research")
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *_, **__: httpx.Response(
            502 if gateway_code not in {"AUTHENTICATION_FAILED", "RATE_LIMITED"} else (401 if gateway_code == "AUTHENTICATION_FAILED" else 429),
            request=request,
            json={"error": {"code": gateway_code, "message": "private upstream detail"}},
        ),
    )
    with pytest.raises(AIResearchProviderError) as error:
        omniroute_provider().research(omniroute_identity(), ["query"])
    assert error.value.code == expected
    assert "private upstream detail" not in str(error.value)
    assert "secret" not in str(error.value)


def test_omniroute_timeout_is_typed(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *_, **__: (_ for _ in ()).throw(httpx.ReadTimeout("slow")))
    with pytest.raises(AIResearchProviderError) as error:
        omniroute_provider().research(omniroute_identity(), ["query"])
    assert error.value.code == ProviderErrorCode.TIMEOUT


@pytest.mark.parametrize(
    "mutation,expected",
    [
        (lambda body: body.update(sources=[]), ProviderErrorCode.SEARCH_UNAVAILABLE),
        (lambda body: body.update(source_content_retrieved=False), ProviderErrorCode.SEARCH_UNAVAILABLE),
        (lambda body: body.update(answer="not-json"), ProviderErrorCode.MALFORMED_RESPONSE),
    ],
)
def test_omniroute_rejects_no_source_or_malformed_success(monkeypatch, mutation, expected):
    request = httpx.Request("POST", "http://omniroute.test/v1/research")
    body = omniroute_success_body()
    mutation(body)
    monkeypatch.setattr(httpx, "post", lambda *_, **__: httpx.Response(200, request=request, json=body))
    with pytest.raises(AIResearchProviderError) as error:
        omniroute_provider().research(omniroute_identity(), ["query"])
    assert error.value.code == expected


def test_omniroute_rejects_unretrieved_citation(monkeypatch):
    request = httpx.Request("POST", "http://omniroute.test/v1/research")
    body = omniroute_success_body()
    parsed = __import__("json").loads(body["answer"])
    parsed["findings"][0]["source_url"] = "https://fabricated.example/movie"
    body["answer"] = __import__("json").dumps(parsed)
    monkeypatch.setattr(httpx, "post", lambda *_, **__: httpx.Response(200, request=request, json=body))
    with pytest.raises(AIResearchProviderError) as error:
        omniroute_provider().research(omniroute_identity(), ["query"])
    assert error.value.code == ProviderErrorCode.MALFORMED_RESPONSE


def test_fallback_is_not_used_for_quota_auth_rate_or_payment():
    class Broken(FakeProvider):
        def __init__(self, code):
            super().__init__()
            self.code = code

        def research(self, identity, queries):
            raise AIResearchProviderError(self.code, "stop")

    for code in (
        ProviderErrorCode.QUOTA_EXCEEDED,
        ProviderErrorCode.AUTHENTICATION_FAILED,
        ProviderErrorCode.RATE_LIMITED,
        ProviderErrorCode.PAYMENT_REQUIRED,
    ):
        fallback = FakeProvider([finding()])
        with pytest.raises(AIResearchProviderError) as error:
            FallbackAIResearchProvider([Broken(code), fallback]).research(omniroute_identity(), ["query"])
        assert error.value.code == code
        assert fallback.identity is None


def test_ai_result_without_inspected_source_cannot_publish(database, monkeypatch):
    movie = database.query(Movie).filter_by(tmdb_id=101).one()
    monkeypatch.setattr("app.services.ott.ai_research.inspect_source", lambda *_: None)
    report = OTTResearchAgent(database, FakeProvider([finding()])).research_movie(movie.id, dry_run=False)
    assert report.decision == ResearchDecision.NEEDS_REVIEW
    assert report.evidence_created == 0
    assert database.query(OttEvidence).count() == 0


def test_correct_official_source_creates_existing_evidence(database, monkeypatch):
    movie = database.query(Movie).filter_by(tmdb_id=101).one()
    monkeypatch.setattr("app.services.ott.ai_research.inspect_source", lambda *_: inspected())
    report = OTTResearchAgent(database, FakeProvider([finding()])).research_movie(movie.id, dry_run=False)
    row = database.query(OttEvidence).one()
    assert report.decision == ResearchDecision.AUTO_ACCEPT
    assert report.evidence_created == 1
    assert row.platform == "Netflix" and row.country == "IN"
    assert row.verification_method == "AI_WEB_RESEARCH"
    assert "test-ai" in row.summary and "web-model" in row.summary


def test_stable_identity_is_input_and_answer_key_is_not(database, monkeypatch):
    movie = database.query(Movie).filter_by(tmdb_id=101).one()
    provider = FakeProvider([])
    OTTResearchAgent(database, provider).research_movie(movie.id)
    assert provider.identity.local_movie_id == movie.id
    assert provider.identity.tmdb_id == 101
    payload = str(provider.identity)
    assert "Netflix" not in payload


@pytest.mark.parametrize("reason", ["same title different movie", "wrong year", "wrong remake"])
def test_identity_mismatch_is_rejected(database, monkeypatch, reason):
    movie = database.query(Movie).filter_by(tmdb_id=101).one()
    monkeypatch.setattr("app.services.ott.ai_research.inspect_source", lambda *_: None)
    report = OTTResearchAgent(database, FakeProvider([finding(evidence_summary=reason)])).research_movie(movie.id)
    assert report.decision == ResearchDecision.NEEDS_REVIEW
    assert "SOURCE_NOT_INSPECTED_OR_IDENTITY_REJECTED" in report.findings[0].rejection_reasons


@pytest.mark.parametrize(
    "summary,requested,expected",
    [
        ("Available to rent on Amazon Video", "SUBSCRIPTION", "RENT"),
        ("Available to buy on Apple TV", "SUBSCRIPTION", "BUY"),
        ("Streaming on Prime Video by subscription", "SUBSCRIPTION", "SUBSCRIPTION"),
        ("Free streaming with ads", "SUBSCRIPTION", "ADS"),
    ],
)
def test_release_type_is_strict(database, monkeypatch, summary, requested, expected):
    movie = database.query(Movie).filter_by(tmdb_id=101).one()
    monkeypatch.setattr(
        "app.services.ott.ai_research.inspect_source",
        lambda *_: inspected(evidence_summary=summary, platform="Prime Video"),
    )
    report = OTTResearchAgent(database, FakeProvider([finding(watch_type=requested)])).research_movie(movie.id)
    assert report.findings[0].watch_type == expected


def test_region_preserved_and_distinct_releases_survive(database, monkeypatch):
    movie = database.query(Movie).filter_by(tmdb_id=101).one()
    calls = iter([inspected(country="IN"), inspected(country="US", url="https://www.netflix.com/us/title/example")])
    monkeypatch.setattr("app.services.ott.ai_research.inspect_source", lambda *_: next(calls))
    provider = FakeProvider([finding(), finding(source_url="https://www.netflix.com/us/title/example", region="US")])
    report = OTTResearchAgent(database, provider).research_movie(movie.id)
    assert {item.region for item in report.findings} == {"IN", "US"}
    assert report.decision == ResearchDecision.AUTO_ACCEPT


def test_admin_verified_data_is_not_overwritten(database, monkeypatch):
    movie = database.query(Movie).filter_by(tmdb_id=101).one()
    current = database.query(OttAvailability).filter_by(movie_id=movie.id).one()
    current.locked_by_admin = current.manually_verified = True
    current.provider = "Netflix"
    current.ott_release_date = date(2026, 9, 20)
    current.verification_status = "CONFIRMED"
    database.commit()
    monkeypatch.setattr(
        "app.services.ott.ai_research.inspect_source",
        lambda *_: inspected(platform="Netflix", release_date="2026-09-18"),
    )
    report = OTTResearchAgent(database, FakeProvider([finding(platform="Netflix")])).research_movie(movie.id, dry_run=False)
    database.refresh(current)
    assert report.decision == ResearchDecision.CONFLICT
    assert current.provider == "Netflix" and current.ott_release_date == date(2026, 9, 20)


def test_trusted_domain_must_match_hostname_boundary():
    from app.services.ott_providers import source_rank

    assert source_rank("https://www.netflix.com/in/title/example")[0] == "official_platform"
    assert source_rank("https://netflix.com.evil.example/phishing")[0] == "unknown"
    assert source_rank("https://evil.example/?next=https://netflix.com/title/1")[0] == "unknown"


@pytest.mark.parametrize(
    "code",
    [ProviderErrorCode.QUOTA_EXCEEDED, ProviderErrorCode.RATE_LIMITED, ProviderErrorCode.TIMEOUT],
)
def test_provider_errors_are_deferred_and_create_no_evidence(database, code):
    class Broken(FakeProvider):
        def research(self, identity, queries):
            raise AIResearchProviderError(code, "provider failed")
    movie = database.query(Movie).filter_by(tmdb_id=101).one()
    report = OTTResearchAgent(database, Broken()).research_movie(movie.id, dry_run=False)
    assert report.decision == ResearchDecision.DEFERRED_PROVIDER_ERROR
    assert report.provider_error == code.value
    assert database.query(OttEvidence).count() == 0


def test_equivalent_source_evidence_is_deduplicated(database, monkeypatch):
    movie = database.query(Movie).filter_by(tmdb_id=101).one()
    monkeypatch.setattr("app.services.ott.ai_research.inspect_source", lambda *_: inspected())
    agent = OTTResearchAgent(database, FakeProvider([finding()]))
    assert agent.research_movie(movie.id, dry_run=False).evidence_created == 1
    assert agent.research_movie(movie.id, dry_run=False).evidence_created == 0
    assert database.query(OttEvidence).count() == 1


def test_schema_validation_and_malformed_output(monkeypatch):
    with pytest.raises(ValueError):
        ProviderFinding.from_mapping({"platform": "Netflix"})
    with pytest.raises(ValueError):
        ProviderFinding.from_mapping({"source_url": "http://example.com"})
    request = httpx.Request("POST", "https://provider.example/research")
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *_, **__: httpx.Response(200, request=request, json={"findings": "bad"}),
    )
    provider = StructuredWebResearchProvider(provider="x", model="m", api_url=str(request.url), api_key="secret")
    movie_identity = MovieResearchIdentity(1, 101, None, "Example Film", None, 2026, "ml")
    with pytest.raises(AIResearchProviderError) as error:
        provider.research(movie_identity, [])
    assert error.value.code == ProviderErrorCode.MALFORMED_RESPONSE
    assert "secret" not in str(error.value)


def test_dry_run_never_changes_canonical_or_evidence(database, monkeypatch):
    movie = database.query(Movie).filter_by(tmdb_id=101).one()
    before = [(row.id, row.provider, row.ott_release_date) for row in database.query(OttAvailability).all()]
    monkeypatch.setattr("app.services.ott.ai_research.inspect_source", lambda *_: inspected())
    report = OTTResearchAgent(database, FakeProvider([finding()])).research_movie(movie.id, dry_run=True)
    after = [(row.id, row.provider, row.ott_release_date) for row in database.query(OttAvailability).all()]
    assert report.decision == ResearchDecision.AUTO_ACCEPT
    assert before == after
    assert database.query(OttEvidence).count() == 0
