"""Provider-neutral AI web research that only trusts inspected web evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from enum import StrEnum
import json
import re
from typing import Protocol
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.services.ai_research_audit import AIResearchAuditService, safe_source_url
from app.models.movie import Movie
from app.models.movie_metadata import ExternalId, MovieProductionCountry, ProductionCountry
from app.models.operations import OttEvidence
from app.models.ott_availability import OttAvailability
from app.services.operations import OttResearchService
from app.services.ott.providers.base import normalize_availability_type
from app.services.ott_providers import inspect_source, normalize_platform, source_rank


class ResearchDecision(StrEnum):
    AUTO_ACCEPT = "AUTO_ACCEPT"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    NO_RELIABLE_RESULT = "NO_RELIABLE_RESULT"
    CONFLICT = "CONFLICT"
    DEFERRED_PROVIDER_ERROR = "DEFERRED_PROVIDER_ERROR"


class ProviderErrorCode(StrEnum):
    QUOTA_EXCEEDED = "PROVIDER_QUOTA_EXCEEDED"
    RATE_LIMITED = "PROVIDER_RATE_LIMITED"
    AUTHENTICATION_FAILED = "PROVIDER_AUTHENTICATION_FAILED"
    PAYMENT_REQUIRED = "PROVIDER_PAYMENT_REQUIRED"
    TIMEOUT = "PROVIDER_TIMEOUT"
    UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    MODEL_UNAVAILABLE = "PROVIDER_MODEL_UNAVAILABLE"
    SEARCH_UNAVAILABLE = "PROVIDER_SEARCH_UNAVAILABLE"
    MALFORMED_RESPONSE = "PROVIDER_MALFORMED_RESPONSE"
    INTERNAL_ERROR = "PROVIDER_INTERNAL_ERROR"


class AIResearchProviderError(RuntimeError):
    def __init__(self, code: ProviderErrorCode, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class MovieResearchIdentity:
    local_movie_id: int
    tmdb_id: int
    imdb_id: str | None
    title: str
    original_title: str | None
    release_year: int | None
    original_language: str | None
    production_countries: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProviderFinding:
    source_url: str
    source_title: str | None = None
    platform: str | None = None
    release_date: str | None = None
    region: str | None = None
    watch_type: str | None = None
    evidence_summary: str | None = None
    identity_confidence: float = 0
    fact_confidence: float = 0
    platform_confidence: float = 0
    date_confidence: float = 0
    query: str | None = None

    @classmethod
    def from_mapping(cls, value: dict) -> "ProviderFinding":
        if not isinstance(value, dict) or not isinstance(value.get("source_url"), str):
            raise ValueError("AI finding requires a source_url")
        allowed = {item.name for item in cls.__dataclass_fields__.values()}
        clean = {key: item for key, item in value.items() if key in allowed}
        finding = cls(**clean)
        parsed = urlparse(finding.source_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("AI finding source_url must be an absolute HTTPS URL")
        if finding.release_date:
            date.fromisoformat(str(finding.release_date)[:10])
        return finding


@dataclass(slots=True)
class AIProviderResult:
    provider: str
    model: str
    findings: list[ProviderFinding]
    queries: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ValidatedFinding:
    platform: str | None
    release_date: date | None
    region: str
    watch_type: str
    source_url: str
    source_title: str | None
    source_type: str
    source_domain: str
    evidence_summary: str
    identity_confidence: float
    confidence: float
    platform_confidence: float
    date_confidence: float
    query: str | None
    inspected: bool
    rejection_reasons: list[str] = field(default_factory=list)
    date_rejection_reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AIResearchReport:
    movie_identity: MovieResearchIdentity
    provider: str
    model: str
    findings: list[ValidatedFinding]
    conflicts: list[str]
    decision: ResearchDecision
    queries: list[str]
    provider_error: str | None = None
    evidence_created: int = 0

    def serializable(self) -> dict:
        data = asdict(self)
        data["decision"] = self.decision.value
        for item in data["findings"]:
            if item["release_date"]:
                item["release_date"] = item["release_date"].isoformat()
        return data


class AIResearchProvider(Protocol):
    name: str
    model: str

    @property
    def configured(self) -> bool: ...

    def research(self, identity: MovieResearchIdentity, queries: list[str]) -> AIProviderResult: ...


class StructuredWebResearchProvider:
    """Generic JSON HTTP adapter for an AI service with live-web capability.

    The backend endpoint must perform live search and return structured JSON.
    Its statements remain untrusted until this application independently opens
    and validates the cited source pages.
    """

    def __init__(self, *, provider=None, model=None, api_url=None, api_key=None, timeout=90):
        self.name = provider if provider is not None else settings.AI_RESEARCH_PROVIDER
        self.model = model if model is not None else settings.AI_RESEARCH_MODEL
        self.api_url = api_url if api_url is not None else settings.AI_RESEARCH_API_URL
        self.api_key = api_key if api_key is not None else settings.AI_RESEARCH_API_KEY
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.name and self.model and self.api_url and self.api_key)

    def research(self, identity: MovieResearchIdentity, queries: list[str]) -> AIProviderResult:
        if not self.configured:
            raise AIResearchProviderError(ProviderErrorCode.UNAVAILABLE, "AI research provider is not configured")
        payload = {
            "model": self.model,
            "task": "ott_web_research",
            "movie_identity": asdict(identity),
            "queries": queries,
            "limits": {"max_calls": settings.AI_RESEARCH_CALLS_PER_MOVIE, "max_sources": 8},
            "requirements": {
                "live_web_required": True,
                "source_urls_required": True,
                "do_not_use_model_memory_as_evidence": True,
                "separate_rent_buy_from_subscription": True,
                "preserve_region": True,
            },
        }
        try:
            response = httpx.post(
                self.api_url,
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout,
                follow_redirects=True,
            )
        except httpx.TimeoutException as exc:
            raise AIResearchProviderError(ProviderErrorCode.TIMEOUT, "AI research provider timed out") from exc
        except httpx.HTTPError as exc:
            raise AIResearchProviderError(ProviderErrorCode.UNAVAILABLE, "AI research provider unavailable") from exc
        if response.status_code == 401:
            raise AIResearchProviderError(ProviderErrorCode.AUTHENTICATION_FAILED, "AI provider authentication failed")
        if response.status_code == 402:
            raise AIResearchProviderError(ProviderErrorCode.QUOTA_EXCEEDED, "AI provider quota/payment limit reached")
        if response.status_code == 429:
            raise AIResearchProviderError(ProviderErrorCode.RATE_LIMITED, "AI provider rate limited")
        if response.status_code >= 500:
            raise AIResearchProviderError(ProviderErrorCode.UNAVAILABLE, "AI research provider unavailable")
        try:
            response.raise_for_status()
            body = response.json()
            raw_findings = body.get("findings")
            if not isinstance(raw_findings, list):
                raise ValueError("findings must be a list")
            findings = [ProviderFinding.from_mapping(item) for item in raw_findings]
            returned_queries = body.get("queries") or queries
            conflicts = body.get("conflicts") or []
            if not isinstance(returned_queries, list) or not isinstance(conflicts, list):
                raise ValueError("queries and conflicts must be lists")
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise AIResearchProviderError(ProviderErrorCode.MALFORMED_RESPONSE, "AI provider returned malformed structured output") from exc
        return AIProviderResult(self.name, self.model, findings, returned_queries, [str(x) for x in conflicts])


class OmniRouteResearchProvider(StructuredWebResearchProvider):
    """Adapter for the hardened OmniRoute ``POST /v1/research`` contract."""

    ERROR_CODES = {
        "AUTHENTICATION_FAILED": ProviderErrorCode.AUTHENTICATION_FAILED,
        "QUOTA_EXCEEDED": ProviderErrorCode.QUOTA_EXCEEDED,
        "RATE_LIMITED": ProviderErrorCode.RATE_LIMITED,
        "PAYMENT_REQUIRED": ProviderErrorCode.PAYMENT_REQUIRED,
        "TIMEOUT": ProviderErrorCode.TIMEOUT,
        "PROVIDER_UNAVAILABLE": ProviderErrorCode.UNAVAILABLE,
        "MODEL_UNAVAILABLE": ProviderErrorCode.MODEL_UNAVAILABLE,
        "SEARCH_UNAVAILABLE": ProviderErrorCode.SEARCH_UNAVAILABLE,
        "MALFORMED_RESPONSE": ProviderErrorCode.MALFORMED_RESPONSE,
        "INTERNAL_ERROR": ProviderErrorCode.INTERNAL_ERROR,
    }

    @staticmethod
    def _question(identity: MovieResearchIdentity) -> str:
        identity_json = json.dumps(asdict(identity), ensure_ascii=True, sort_keys=True)
        return (
            "Research current India OTT availability for exactly this movie identity: "
            f"{identity_json}. Do not infer from title alone and do not use model memory as evidence. "
            "Use only retrieved pages. Distinguish subscription, ad-supported, free streaming, "
            "digital rent/buy, PVOD, and theatrical availability. Platform and exact OTT date are "
            "separate facts; never use article publication, page update, theatrical, or rent/buy dates "
            "as a subscription OTT date. The answer string must be exactly one compact JSON object, "
            "without Markdown, shaped as {\"findings\":[{\"source_url\":\"https://...\","
            "\"platform\":\"...\",\"release_date\":\"YYYY-MM-DD or null\",\"region\":\"IN or UNKNOWN\","
            "\"watch_type\":\"SUBSCRIPTION|AD_SUPPORTED|FREE_STREAMING|DIGITAL_RENT_BUY|PVOD|THEATRICAL|UNKNOWN\","
            "\"evidence_summary\":\"claim supported by that page\",\"identity_confidence\":0,"
            "\"platform_confidence\":0,\"date_confidence\":0,\"query\":\"...\"}],\"conflicts\":[]}. "
            "Every source_url must exactly match one of the retrieved source URLs. Omit unsupported facts."
        )

    @classmethod
    def _provider_error(cls, response: httpx.Response) -> AIResearchProviderError:
        try:
            error = response.json().get("error") or {}
        except (ValueError, json.JSONDecodeError):
            error = {}
        gateway_code = str(error.get("code") or "").upper()
        if gateway_code in cls.ERROR_CODES:
            code = cls.ERROR_CODES[gateway_code]
        elif response.status_code in {401, 403}:
            code = ProviderErrorCode.AUTHENTICATION_FAILED
        elif response.status_code == 402:
            code = ProviderErrorCode.PAYMENT_REQUIRED
        elif response.status_code == 429:
            code = ProviderErrorCode.RATE_LIMITED
        elif response.status_code in {408, 504}:
            code = ProviderErrorCode.TIMEOUT
        elif response.status_code >= 500:
            code = ProviderErrorCode.UNAVAILABLE
        else:
            code = ProviderErrorCode.INTERNAL_ERROR
        return AIResearchProviderError(code, f"OmniRoute research failed: {code.value}")

    def research(self, identity: MovieResearchIdentity, queries: list[str]) -> AIProviderResult:
        if not self.configured:
            raise AIResearchProviderError(ProviderErrorCode.UNAVAILABLE, "OmniRoute research is not configured")
        payload = {
            "question": self._question(identity),
            "queries": queries,
            "max_sources": min(8, max(2, len(queries) * 2)),
            "model": self.model,
        }
        try:
            response = httpx.post(
                self.api_url,
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout,
                follow_redirects=False,
            )
        except httpx.TimeoutException as exc:
            raise AIResearchProviderError(ProviderErrorCode.TIMEOUT, "OmniRoute research timed out") from exc
        except httpx.HTTPError as exc:
            raise AIResearchProviderError(ProviderErrorCode.UNAVAILABLE, "OmniRoute research unavailable") from exc
        if not response.is_success:
            raise self._provider_error(response)
        try:
            body = response.json()
            if (
                body.get("mode") != "WEB_RESEARCH"
                or body.get("search_executed") is not True
                or body.get("source_content_retrieved") is not True
            ):
                raise AIResearchProviderError(
                    ProviderErrorCode.SEARCH_UNAVAILABLE,
                    "OmniRoute did not prove live source retrieval",
                )
            sources = body.get("sources")
            if not isinstance(sources, list) or not sources:
                raise AIResearchProviderError(
                    ProviderErrorCode.SEARCH_UNAVAILABLE,
                    "OmniRoute returned no retrieved sources",
                )
            source_map = {}
            for source in sources:
                if not isinstance(source, dict):
                    continue
                source_url = source.get("url")
                parsed = urlparse(str(source_url or ""))
                if parsed.scheme == "https" and parsed.hostname:
                    source_map[str(source_url)] = source
            if not source_map:
                raise AIResearchProviderError(
                    ProviderErrorCode.SEARCH_UNAVAILABLE,
                    "OmniRoute returned no usable HTTPS sources",
                )
            answer = body.get("answer")
            if not isinstance(answer, str) or not answer.strip().startswith("{"):
                raise ValueError("answer must be an unwrapped JSON object string")
            parsed_answer = json.loads(answer)
            raw_findings = parsed_answer.get("findings")
            conflicts = parsed_answer.get("conflicts") or []
            if not isinstance(raw_findings, list) or not isinstance(conflicts, list):
                raise ValueError("answer findings/conflicts must be lists")
            findings = []
            for item in raw_findings:
                if not isinstance(item, dict) or item.get("source_url") not in source_map:
                    raise ValueError("finding cites a URL that OmniRoute did not retrieve")
                source = source_map[item["source_url"]]
                enriched = {
                    **item,
                    "source_title": item.get("source_title") or source.get("title"),
                    "fact_confidence": max(
                        float(item.get("platform_confidence") or 0),
                        float(item.get("date_confidence") or 0),
                    ),
                }
                findings.append(ProviderFinding.from_mapping(enriched))
        except AIResearchProviderError:
            raise
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise AIResearchProviderError(
                ProviderErrorCode.MALFORMED_RESPONSE,
                "OmniRoute returned malformed structured research",
            ) from exc
        return AIProviderResult(
            str(body.get("provider") or self.name),
            str(body.get("model") or self.model),
            findings,
            queries,
            [str(item) for item in conflicts],
        )


class FallbackAIResearchProvider:
    """Try configured providers once each; never spin in an unbounded retry loop."""

    def __init__(self, providers: list[AIResearchProvider]):
        self.providers = [provider for provider in providers if provider.configured]
        self.name = " -> ".join(provider.name for provider in self.providers)
        self.model = " -> ".join(provider.model for provider in self.providers)
        self.last_events: list[dict] = []

    @property
    def configured(self) -> bool:
        return bool(self.providers)

    def research(self, identity: MovieResearchIdentity, queries: list[str]) -> AIProviderResult:
        self.last_events = []
        last_error = None
        fallback_codes = {
            ProviderErrorCode.TIMEOUT,
            ProviderErrorCode.UNAVAILABLE,
            ProviderErrorCode.MODEL_UNAVAILABLE,
            ProviderErrorCode.MALFORMED_RESPONSE,
        }
        for index, provider in enumerate(self.providers):
            try:
                return provider.research(identity, queries)
            except AIResearchProviderError as exc:
                last_error = exc
                self.last_events.append({
                    "type": "PROVIDER_ERROR",
                    "provider": provider.name,
                    "model": provider.model,
                    "error_type": exc.code.value,
                })
                if exc.code not in fallback_codes:
                    raise
                if index + 1 < len(self.providers):
                    next_provider = self.providers[index + 1]
                    self.last_events.append({
                        "type": "PROVIDER_FALLBACK",
                        "from_provider": provider.name,
                        "to_provider": next_provider.name,
                        "error_type": exc.code.value,
                    })
        raise last_error or AIResearchProviderError(
            ProviderErrorCode.UNAVAILABLE, "No AI research provider is configured"
        )


def configured_ai_research_provider() -> AIResearchProvider:
    primary_class = (
        OmniRouteResearchProvider
        if settings.AI_RESEARCH_PROVIDER.strip().lower() == "omniroute"
        or settings.AI_RESEARCH_API_URL.rstrip("/").endswith("/v1/research")
        else StructuredWebResearchProvider
    )
    return FallbackAIResearchProvider([
        primary_class(),
        StructuredWebResearchProvider(
            provider=settings.AI_RESEARCH_FALLBACK_PROVIDER,
            model=settings.AI_RESEARCH_FALLBACK_MODEL,
            api_url=settings.AI_RESEARCH_FALLBACK_API_URL,
            api_key=settings.AI_RESEARCH_FALLBACK_API_KEY,
        ),
    ])


class OTTResearchAgent:
    """Research one stable movie identity and feed only verified sources to reconciliation."""

    def __init__(
        self,
        db: Session,
        provider: AIResearchProvider | None = None,
        audit_service: AIResearchAuditService | None = None,
    ):
        self.db = db
        self.provider = provider or configured_ai_research_provider()
        self.audit = audit_service or AIResearchAuditService(db)

    def _availability_snapshot(self, movie_id: int) -> dict:
        rows = self.db.query(OttAvailability).filter_by(movie_id=movie_id).order_by(OttAvailability.id).all()
        return {
            "availability": [
                {
                    "id": row.id,
                    "platform": row.provider,
                    "date": row.ott_release_date.isoformat() if row.ott_release_date else None,
                    "region": row.country,
                    "watch_type": row.watch_type,
                    "verification_status": row.verification_status,
                    "admin_protected": bool(row.manually_verified or row.locked_by_admin),
                }
                for row in rows
            ]
        }

    def _record_provider_events(self, run_id: str, activity_id: int, movie_id: int) -> None:
        for item in getattr(self.provider, "last_events", []) or []:
            self.audit.provider_event(
                run_id,
                activity_id,
                movie_id,
                item,
            )

    def identity_for(self, movie: Movie) -> MovieResearchIdentity:
        imdb_id = self.db.query(ExternalId.external_id).filter(
            ExternalId.movie_id == movie.id,
            ExternalId.provider.ilike("imdb"),
        ).scalar()
        release = movie.theatrical_release_date or movie.release_date
        production_countries = tuple(
            country
            for (country,) in self.db.query(ProductionCountry.iso_3166_1)
            .join(
                MovieProductionCountry,
                MovieProductionCountry.production_country_id == ProductionCountry.id,
            )
            .filter(MovieProductionCountry.movie_id == movie.id)
            .order_by(ProductionCountry.iso_3166_1)
            .all()
        )
        return MovieResearchIdentity(
            local_movie_id=movie.id,
            tmdb_id=movie.tmdb_id,
            imdb_id=imdb_id,
            title=movie.title,
            original_title=movie.original_title,
            release_year=release.year if release else None,
            original_language=movie.original_language,
            production_countries=production_countries,
        )

    @staticmethod
    def build_queries(identity: MovieResearchIdentity) -> list[str]:
        original = f' "{identity.original_title}"' if identity.original_title and identity.original_title != identity.title else ""
        stable = " ".join(
            value
            for value in (
                f"TMDB {identity.tmdb_id}",
                identity.imdb_id,
                str(identity.release_year or ""),
                identity.original_language or "",
            )
            if value
        )
        base = f'"{identity.title}"{original} {stable}'.strip()
        queries = [
            f"{base} OTT release date streaming India",
            f"{base} Netflix Prime Video JioHotstar ZEE5 SonyLIV aha Sun NXT ManoramaMAX India",
            f"{base} digital release rent buy subscription streaming",
        ]
        return queries[: max(1, settings.AI_RESEARCH_CALLS_PER_MOVIE)]

    @staticmethod
    def _watch_type(text: str, platform: str | None, requested: str | None) -> str:
        combined = f"{requested or ''} {text}".lower()
        if re.search(r"\b(?:rent|rental|buy|purchase|pvod|video on demand)\b", combined):
            if re.search(r"\b(?:buy|purchase)\b", combined):
                return "BUY"
            return "RENT"
        if re.search(r"\b(?:ad[- ]supported|with ads)\b", combined):
            return "ADS"
        if re.search(r"\bfree(?: streaming| to watch)?\b", combined):
            return "FREE"
        if normalize_availability_type(requested) == "SUBSCRIPTION" or re.search(
            r"\b(?:subscription|streaming on|premiere(?:s|d)? on|available on)\b", combined
        ):
            # Amazon Video is not Prime Video unless the inspected evidence says Prime.
            if platform == "Prime Video" and re.search(r"\bamazon video\b", combined) and not re.search(r"\bprime video\b", combined):
                return "RENT"
            return "SUBSCRIPTION"
        return "UNKNOWN"

    def _validate(self, movie: Movie, finding: ProviderFinding) -> ValidatedFinding:
        rank, rank_score = source_rank(finding.source_url)
        inspected = None
        reasons: list[str] = []
        date_reasons: list[str] = []
        if rank == "unknown":
            reasons.append("SOURCE_NOT_APPROVED")
        else:
            try:
                inspected = inspect_source(movie, {
                    "url": finding.source_url,
                    "title": finding.source_title,
                    "snippet": finding.evidence_summary or "",
                    "country": finding.region,
                })
            except (httpx.HTTPError, ValueError):
                reasons.append("SOURCE_FETCH_FAILED")
        if not inspected and "SOURCE_FETCH_FAILED" not in reasons:
            reasons.append("SOURCE_NOT_INSPECTED_OR_IDENTITY_REJECTED")
        inspected = inspected or {}
        platform = normalize_platform(inspected.get("platform"))
        parsed_date = None
        if inspected.get("release_date"):
            try:
                parsed_date = date.fromisoformat(str(inspected["release_date"])[:10])
            except ValueError:
                date_reasons.append("INVALID_DATE")
        elif finding.release_date:
            date_reasons.append("DATE_NOT_SUPPORTED_BY_INSPECTED_SOURCE")
        region = (inspected.get("country") or "UNKNOWN").upper()
        summary = str(inspected.get("evidence_summary") or "")[:1000]
        watch_type = self._watch_type(summary, platform, finding.watch_type)
        identity_confidence = min(100.0, max(0.0, float(finding.identity_confidence or 0)))
        platform_confidence = min(
            float(rank_score),
            float(finding.platform_confidence or finding.fact_confidence or rank_score),
        )
        date_confidence = (
            min(
                float(rank_score),
                float(finding.date_confidence or finding.fact_confidence or rank_score),
            )
            if parsed_date
            else 0.0
        )
        if identity_confidence < 85:
            reasons.append("IDENTITY_CONFIDENCE_BELOW_85")
        if not platform:
            reasons.append("PLATFORM_NOT_EXTRACTED")
        if region == "UNKNOWN":
            reasons.append("REGION_NOT_VERIFIED")
        if watch_type == "UNKNOWN":
            reasons.append("WATCH_TYPE_NOT_VERIFIED")
        if platform_confidence < settings.OTT_CONFIRMATION_THRESHOLD:
            reasons.append("PLATFORM_CONFIDENCE_BELOW_AUTO_ACCEPT_THRESHOLD")
        if parsed_date and date_confidence < settings.OTT_CONFIRMATION_THRESHOLD:
            date_reasons.append("DATE_CONFIDENCE_BELOW_AUTO_ACCEPT_THRESHOLD")
            parsed_date = None
        return ValidatedFinding(
            platform, parsed_date, region, watch_type, finding.source_url,
            finding.source_title, rank, urlparse(finding.source_url).hostname or "",
            summary, identity_confidence, platform_confidence,
            platform_confidence, date_confidence, finding.query,
            bool(inspected), list(dict.fromkeys(reasons)), list(dict.fromkeys(date_reasons)),
        )

    def research_movie(self, movie_id: int, *, dry_run: bool = True, research_run_id: str | None = None) -> AIResearchReport:
        movie = self.db.get(Movie, movie_id)
        if not movie:
            raise LookupError("Movie not found")
        identity = self.identity_for(movie)
        queries = self.build_queries(identity)
        auto_run = research_run_id is None
        run = self.audit.create_run(
            run_id=research_run_id,
            trigger="MANUAL",
            initiated_by="ai-research-agent",
            mode="DRY_RUN" if dry_run else "LIVE",
            provider=getattr(self.provider, "name", ""),
            model=getattr(self.provider, "model", ""),
            movies_total=1,
        )
        activity = self.audit.start_movie(
            run.run_id,
            identity,
            dry_run=dry_run,
            provider=getattr(self.provider, "name", ""),
            model=getattr(self.provider, "model", ""),
        )
        self.audit.set_queries(activity.id, queries)
        before_value = self._availability_snapshot(movie_id)
        self.audit.event(
            run.run_id,
            "SEARCH_STARTED",
            "Live web research started",
            movie_activity_id=activity.id,
            movie_id=movie_id,
            metadata={"query_count": len(queries)},
        )
        try:
            raw = self.provider.research(identity, queries)
        except AIResearchProviderError as exc:
            self._record_provider_events(run.run_id, activity.id, movie_id)
            report = AIResearchReport(identity, getattr(self.provider, "name", ""), getattr(self.provider, "model", ""), [], [], ResearchDecision.DEFERRED_PROVIDER_ERROR, queries, exc.code.value)
            self.audit.complete_movie(
                activity.id,
                report,
                before_value=before_value,
                proposed_value=None,
                after_value=self._availability_snapshot(movie_id),
                database_changed=False,
            )
            if auto_run:
                self.audit.complete_run(run.run_id)
            return report

        self._record_provider_events(run.run_id, activity.id, movie_id)

        self.audit.event(
            run.run_id,
            "SEARCH_COMPLETED",
            "Live web research completed",
            movie_activity_id=activity.id,
            movie_id=movie_id,
            metadata={"provider": raw.provider, "model": raw.model, "sources_discovered": len(raw.findings)},
        )

        validated = []
        for item in raw.findings:
            validated_item = self._validate(movie, item)
            validated.append(validated_item)
        accepted = [item for item in validated if item.inspected and not item.rejection_reasons]
        # Two independent inspected reputable sources may corroborate a fact
        # that neither source is authoritative enough to publish alone.
        corroboration_groups: dict[tuple, list[ValidatedFinding]] = {}
        for item in validated:
            if item.inspected and item.rejection_reasons == ["PLATFORM_CONFIDENCE_BELOW_AUTO_ACCEPT_THRESHOLD"]:
                key = (normalize_platform(item.platform), item.release_date, item.region, item.watch_type)
                corroboration_groups.setdefault(key, []).append(item)
        for group in corroboration_groups.values():
            if len({item.source_domain for item in group}) >= 2:
                for item in group:
                    item.rejection_reasons.clear()
                    accepted.append(item)
        for item in validated:
            self.audit.record_source(activity.id, item)
        self.audit.event(
            run.run_id,
            "CORROBORATION_CHECK",
            "Independent-source corroboration evaluated",
            movie_activity_id=activity.id,
            movie_id=movie_id,
            metadata={"candidate_groups": len(corroboration_groups), "accepted_findings": len(accepted)},
        )
        conflicts = list(raw.conflicts)
        keys: dict[tuple, set[date | None]] = {}
        for item in accepted:
            key = (normalize_platform(item.platform), item.region, item.watch_type)
            keys.setdefault(key, set()).add(item.release_date)
        if any(len(dates) > 1 for dates in keys.values()):
            conflicts.append("Inspected sources disagree on the date for the same platform, region, and watch type")
        manual = self.db.query(OttAvailability).filter(
            OttAvailability.movie_id == movie_id,
            OttAvailability.locked_by_admin.is_(True),
        ).all()
        for item in accepted:
            if any(
                row.country == item.region
                and normalize_platform(row.provider) == item.platform
                and row.watch_type == item.watch_type.lower()
                and row.ott_release_date
                and item.release_date
                and row.ott_release_date != item.release_date
                for row in manual
            ):
                conflicts.append("AI evidence conflicts with administrator-verified data")
        if conflicts:
            decision = ResearchDecision.CONFLICT
        elif accepted:
            decision = ResearchDecision.AUTO_ACCEPT
        elif validated:
            decision = ResearchDecision.NEEDS_REVIEW
        else:
            decision = ResearchDecision.NO_RELIABLE_RESULT
        report = AIResearchReport(identity, raw.provider, raw.model, validated, list(dict.fromkeys(conflicts)), decision, raw.queries)
        proposed_value = {
            "findings": [
                {
                    "platform": item.platform,
                    "date": item.release_date.isoformat() if item.release_date else None,
                    "region": item.region,
                    "watch_type": item.watch_type,
                    "source_url": safe_source_url(item.source_url),
                }
                for item in accepted
            ]
        }
        if dry_run or decision not in {ResearchDecision.AUTO_ACCEPT, ResearchDecision.CONFLICT}:
            self.audit.complete_movie(
                activity.id,
                report,
                before_value=before_value,
                proposed_value=proposed_value,
                after_value=self._availability_snapshot(movie_id),
                database_changed=False,
            )
            if auto_run:
                self.audit.complete_run(run.run_id)
            return report

        service = OttResearchService(self.db, settings.OTT_CONFIRMATION_THRESHOLD)
        for item in accepted:
            duplicate = self.db.query(OttEvidence.id).filter(
                OttEvidence.movie_id == movie_id,
                OttEvidence.platform == item.platform,
                OttEvidence.release_date == item.release_date,
                OttEvidence.country == item.region,
                OttEvidence.availability_type == item.watch_type,
                OttEvidence.source_url == item.source_url,
            ).first()
            if duplicate:
                continue
            evidence = service.record_evidence(
                movie_id,
                platform=item.platform,
                release_date=item.release_date,
                source_url=item.source_url,
                source_title=item.source_title,
                confidence=item.confidence,
                summary=json.dumps({
                    "provider": raw.provider, "model": raw.model,
                    "query": item.query, "evidence": item.evidence_summary[:500],
                }, ensure_ascii=True),
                source_type=item.source_type,
                source_name=item.source_domain,
                country=item.region,
                inspected=True,
                availability_type=item.watch_type,
                movie_match_confidence=item.identity_confidence,
                platform_confidence=item.platform_confidence,
                date_confidence=item.date_confidence,
                verification_method="AI_WEB_RESEARCH",
                research_run_id=research_run_id,
                allow_publication=not conflicts,
            )
            if conflicts:
                evidence.status = "CONFLICTING"
                self.db.commit()
            report.evidence_created += 1
        after_value = self._availability_snapshot(movie_id)
        self.audit.complete_movie(
            activity.id,
            report,
            before_value=before_value,
            proposed_value=proposed_value,
            after_value=after_value,
            database_changed=before_value != after_value,
        )
        if auto_run:
            self.audit.complete_run(run.run_id)
        return report
