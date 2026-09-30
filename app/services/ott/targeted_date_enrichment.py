"""Checkpointed, date-specific OTT enrichment for priority catalogue titles.

The service deliberately separates discovery from publication.  Curated release
lists may create evidence, but an OTT date is published only when two independent
domains agree (the normal reconciliation rule), or when a human-verified manifest
is supplied for a bounded quality test.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import re
import time
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import case, exists, func, or_
from sqlalchemy.orm import Session

from app.core.secrets import sanitize_error
from app.models.movie import Movie
from app.models.movie_metadata import ExternalId
from app.models.operations import BackfillRecord, OperationState, OttEvidence
from app.models.ott_availability import OttAvailability
from app.services.operations import OttResearchService
from app.services.ott_providers import normalize_platform


OPERATION = "operations.ott_date_targeted_enrichment"
CONFIRMED_OTT = ("CONFIRMED", "PLATFORM_CONFIRMED", "VERIFIED")
LANGUAGE_PRIORITY = {"ml": 1, "ta": 2, "te": 3, "hi": 4, "kn": 5, "en": 6, "ko": 7, "ja": 8}

# These are editorial release-date indexes, not search-result pages.  Every row
# is identity-checked against the local title and release year before it becomes
# evidence.  Two independent domains must agree before an automated date can be
# published.
CURATED_LISTS = {
    "ml": (
        "https://cinebuds.com/malayalam-movies-ott-release-dates/",
        "https://www.keralatv.in/malayalam-ott-release-dates/",
        "https://www.bollymoviereviewz.com/2026/01/latest-new-ott-movie-releaseses-release-dates.html",
        "https://www.telugubulletin.com/ott-telugu-movies-online-streaming-release-dates-list",
    ),
    "ta": (
        "https://cinebuds.com/tamil-movies-digital-release-dates/",
        "https://www.bollymoviereviewz.com/2026/01/latest-new-ott-movie-releaseses-release-dates.html",
        "https://www.telugubulletin.com/ott-telugu-movies-online-streaming-release-dates-list",
    ),
    "te": (
        "https://cinebuds.com/telugu-movies-digital-release-dates/",
        "https://trendraja.in/telugu-movie-ott-release-dates-2021/",
        "https://www.bollymoviereviewz.com/2026/01/latest-new-ott-movie-releaseses-release-dates.html",
        "https://www.telugubulletin.com/ott-telugu-movies-online-streaming-release-dates-list",
    ),
    "hi": (
        "https://cinebuds.com/bollywood-movies-digital-release-dates/",
        "https://www.bollymoviereviewz.com/2026/01/latest-new-ott-movie-releaseses-release-dates.html",
        "https://www.telugubulletin.com/ott-telugu-movies-online-streaming-release-dates-list",
    ),
    "kn": (
        "https://cinebuds.com/upcoming-kannada-movies-on-ott-platforms/",
        "https://www.bollymoviereviewz.com/2026/01/latest-new-ott-movie-releaseses-release-dates.html",
        "https://www.telugubulletin.com/ott-telugu-movies-online-streaming-release-dates-list",
    ),
}

LEGAL_WATCH_DOMAINS = (
    "netflix.com", "primevideo.com", "jiohotstar.com", "hotstar.com",
    "jiocinema.com", "zee5.com", "sonyliv.com", "sunnxt.com", "aha.video",
    "manoramamax.com", "etvwin.com", "simplysouth.tv", "lionsgateplay.com",
    "hoichoi.tv", "chaupal.tv", "tentkotta.com", "tv.apple.com",
    "mxplayer.in", "justwatch.com", "themoviedb.org",
)


def is_legal_watch_url(value: str | None) -> bool:
    parsed = urlparse(value or "")
    host = (parsed.hostname or "").casefold()
    if not any(host == domain or host.endswith(f".{domain}") for domain in LEGAL_WATCH_DOMAINS):
        return False
    return host != "themoviedb.org" and not host.endswith(".themoviedb.org") or "/watch" in parsed.path


def normalized_title(value: str | None) -> str:
    value = re.sub(
        r"\s*\((?:malayalam|tamil|telugu|hindi|kannada|dub(?:bed)?)\)\s*$",
        "",
        value or "",
        flags=re.IGNORECASE,
    )
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def parse_release_date(value: str | None) -> date | None:
    text = re.sub(r"\s+", " ", (value or "").strip())
    text = re.sub(r"^(?:on|from|streaming)\s+", "", text, flags=re.IGNORECASE)
    for fmt in (
        "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d %Y", "%B %d, %Y",
        "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y",
    ):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def split_platforms(value: str | None) -> tuple[str, ...]:
    cleaned = re.sub(r"^watch\s+on\s+", "", value or "", flags=re.IGNORECASE)
    cleaned = cleaned.replace("Lions Gate Play", "Lionsgate Play")
    parts = re.split(r"\s*(?:,|&|\band\b|\+)\s*", cleaned, flags=re.IGNORECASE)
    normalized = [normalize_platform(part) for part in parts if part.strip()]
    return tuple(dict.fromkeys(part for part in normalized if part))


@dataclass(frozen=True)
class CuratedRelease:
    title: str
    platform: str
    release_date: date
    source_url: str
    source_title: str
    language: str | None = None


def _column(headers: list[str], *terms: str) -> int | None:
    for index, header in enumerate(headers):
        lowered = header.casefold()
        if any(term in lowered for term in terms):
            return index
    return None


def parse_curated_page(
    html: bytes,
    *,
    source_url: str,
    source_title: str,
    default_language: str | None,
) -> list[CuratedRelease]:
    """Parse conventional editorial tables without guessing from prose."""
    soup = BeautifulSoup(html, "lxml")
    found: list[CuratedRelease] = []
    seen: set[tuple[str, str, date, str]] = set()
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        headers = [cell.get_text(" ", strip=True) for cell in rows[0].find_all(["th", "td"])]
        title_index = _column(headers, "movie", "film", "title", "name")
        platform_index = _column(headers, "platform", "streaming partner", "digital streaming rights")
        date_index = _column(headers, "date")
        language_index = _column(headers, "language")
        if title_index is None or platform_index is None or date_index is None:
            continue
        for row in rows[1:]:
            cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
            required = max(title_index, platform_index, date_index)
            if len(cells) <= required:
                continue
            release_date = parse_release_date(cells[date_index])
            if not release_date:
                continue
            language = default_language
            if language_index is not None and len(cells) > language_index:
                language_name = cells[language_index].casefold()
                language = next(
                    (
                        code
                        for code, name in {
                            "ml": "malayalam", "ta": "tamil", "te": "telugu",
                            "hi": "hindi", "kn": "kannada", "en": "english",
                        }.items()
                        if name in language_name
                    ),
                    "__unmatched__",
                )
            for platform in split_platforms(cells[platform_index]):
                key = (normalized_title(cells[title_index]), platform, release_date, source_url)
                if not key[0] or key in seen:
                    continue
                seen.add(key)
                found.append(
                    CuratedRelease(
                        title=cells[title_index],
                        platform=platform,
                        release_date=release_date,
                        source_url=source_url,
                        source_title=source_title,
                        language=language,
                    )
                )
    return found


class CuratedReleaseIndex:
    def __init__(self, timeout: float = 25, pause_seconds: float = 0.2):
        self.timeout = timeout
        self.pause_seconds = pause_seconds
        self.entries: dict[str, list[CuratedRelease]] = {}
        self.failures: list[str] = []

    def load(self, languages: set[str]) -> None:
        client = httpx.Client(
            timeout=self.timeout,
            follow_redirects=True,
            headers={"User-Agent": "IndianOTTTracker/1.0 (+public-source-verification)"},
        )
        try:
            requested: dict[str, set[str]] = {}
            for language in languages:
                for url in CURATED_LISTS.get(language, ()):
                    requested.setdefault(url, set()).add(language)
            for url, source_languages in requested.items():
                try:
                    response = client.get(url)
                    response.raise_for_status()
                    soup = BeautifulSoup(response.content, "lxml")
                    title = soup.title.get_text(" ", strip=True) if soup.title else urlparse(url).hostname or url
                    default_language = next(iter(source_languages)) if len(source_languages) == 1 else None
                    for entry in parse_curated_page(
                        response.content,
                        source_url=url,
                        source_title=title[:500],
                        default_language=default_language,
                    ):
                        self.entries.setdefault(normalized_title(entry.title), []).append(entry)
                except (httpx.HTTPError, TimeoutError, ConnectionError) as exc:
                    self.failures.append(f"{urlparse(url).hostname}:{sanitize_error(exc)}")
                time.sleep(self.pause_seconds)
        finally:
            client.close()

    def matches(self, movie: Movie) -> list[CuratedRelease]:
        keys = {normalized_title(movie.title), normalized_title(movie.original_title)} - {""}
        release = movie.theatrical_release_date or movie.release_date
        rows = [row for key in keys for row in self.entries.get(key, ())]
        return [
            row for row in rows
            if (not row.language or row.language == movie.original_language)
            and (not release or row.release_date >= release)
            and (not release or row.release_date.year in {release.year, release.year + 1})
        ]


class TargetedOttDateEnrichmentService:
    operation = OPERATION

    def __init__(self, db: Session):
        self.db = db
        self.research = OttResearchService(db)

    def _state(self) -> OperationState:
        state = self.db.query(OperationState).filter_by(name=self.operation).first()
        if state is None:
            state = OperationState(name=self.operation, details={})
            self.db.add(state)
            self.db.flush()
        return state

    @staticmethod
    def _incomplete():
        has_platform = exists().where(
            OttAvailability.movie_id == Movie.id,
            func.upper(OttAvailability.country) == "IN",
        )
        has_date = exists().where(
            OttAvailability.movie_id == Movie.id,
            func.upper(OttAvailability.country) == "IN",
            OttAvailability.ott_release_date.is_not(None),
        )
        has_url = exists().where(
            OttAvailability.movie_id == Movie.id,
            func.upper(OttAvailability.country) == "IN",
            OttAvailability.source_url.is_not(None),
            OttAvailability.source_url != "",
        )
        return or_(~has_platform, ~has_date, ~has_url)

    def targets(self, limit: int = 500) -> list[Movie]:
        attempted = exists().where(
            BackfillRecord.operation == self.operation,
            BackfillRecord.entity_type == "movie",
            BackfillRecord.entity_id == Movie.id,
        )
        release = func.coalesce(Movie.theatrical_release_date, Movie.release_date)
        has_imdb = exists().where(
            ExternalId.movie_id == Movie.id,
            func.lower(ExternalId.provider).in_(("imdb", "imdb_id")),
            ExternalId.external_id.is_not(None),
            ExternalId.external_id != "",
        )
        language_rank = case(
            *[(Movie.original_language == code, rank) for code, rank in LANGUAGE_PRIORITY.items()],
            else_=9,
        )
        return (
            self.db.query(Movie)
            .filter(
                release.is_not(None),
                release <= date.today(),
                self._incomplete(),
                ~attempted,
            )
            .order_by(
                case(
                    (func.extract("year", release) == 2026, 1),
                    (func.extract("year", release) == 2025, 2),
                    (func.extract("year", release) == 2024, 3),
                    else_=4,
                ),
                language_rank,
                case((Movie.theatrical_release_date.is_not(None), 0), else_=1),
                case((Movie.tmdb_id.is_not(None), 0), else_=1),
                case((has_imdb, 0), else_=1),
                Movie.popularity.desc().nullslast(),
                release.desc(),
                Movie.id,
            )
            .limit(max(1, min(int(limit), 500)))
            .all()
        )

    def _record(self, movie_id: int) -> BackfillRecord:
        record = self.db.query(BackfillRecord).filter_by(
            operation=self.operation, entity_type="movie", entity_id=movie_id
        ).first()
        if record is None:
            record = BackfillRecord(operation=self.operation, entity_type="movie", entity_id=movie_id)
            self.db.add(record)
            self.db.flush()
        return record

    def _snapshot(self, movie_id: int) -> dict:
        rows = self.db.query(OttAvailability).filter(
            OttAvailability.movie_id == movie_id,
            func.upper(OttAvailability.country) == "IN",
        ).all()
        return {
            "platforms": {normalize_platform(row.provider) for row in rows},
            "dates": {row.id for row in rows if row.ott_release_date},
            "urls": {row.id for row in rows if is_legal_watch_url(row.source_url)},
            "confirmed_date": any(row.ott_release_date and row.verification_status == "CONFIRMED" for row in rows),
        }

    def _existing_evidence(self, movie_id: int, entry: CuratedRelease) -> bool:
        return self.db.query(OttEvidence.id).filter_by(
            movie_id=movie_id,
            platform=entry.platform,
            release_date=entry.release_date,
            source_url=entry.source_url,
        ).first() is not None

    def _save_entry(self, movie: Movie, entry: CuratedRelease, *, manual: bool = False) -> bool:
        if self._existing_evidence(movie.id, entry):
            return False
        prior_urls = {
            normalize_platform(row.provider): row.source_url
            for row in self.db.query(OttAvailability).filter_by(movie_id=movie.id, country="IN").all()
            if is_legal_watch_url(row.source_url)
        }
        self.research.record_evidence(
            movie.id,
            platform=entry.platform,
            release_date=entry.release_date,
            source_url=entry.source_url,
            source_title=entry.source_title,
            source_name=urlparse(entry.source_url).hostname,
            source_type="manual" if manual else "established_publication",
            country="IN",
            inspected=True,
            manually_verified=manual,
            trusted=manual,
            confidence=100 if manual else 80,
            platform_confidence=100 if manual else 80,
            date_confidence=100 if manual else 80,
            movie_match_confidence=100,
            verification_method="MANUAL_WEB_RESEARCH" if manual else "CURATED_DATE_LIST",
            availability_type="SUBSCRIPTION",
            summary=f"Exact title/year match on date-specific OTT release index: {entry.title}",
            research_run_id="targeted-ott-date",
        )
        # Keep a pre-existing legal watch destination on the canonical row.
        # The editorial article remains attached to OttEvidence as provenance.
        canonical_rows = self.db.query(OttAvailability).filter_by(movie_id=movie.id, country="IN").all()
        for canonical in canonical_rows:
            # Editorial evidence belongs in OttEvidence.  It must never be
            # exposed as the legal watch destination on an availability row.
            if canonical.source_url == entry.source_url and not is_legal_watch_url(canonical.source_url):
                canonical.source_url = prior_urls.get(normalize_platform(canonical.provider))
        self.db.commit()
        return True

    def record_verified_trial(self, rows: list[dict]) -> dict:
        report = {"test_movies": len(rows), "platforms_found": 0, "ott_dates_found": 0, "legal_urls_found": 0, "no_result": 0, "false_matches": 0, "ambiguous": 0}
        for item in rows:
            movie = self.db.get(Movie, int(item["movie_id"]))
            if not movie:
                report["false_matches"] += 1
                continue
            record = self._record(movie.id)
            record.status = "RUNNING"
            record.attempts = (record.attempts or 0) + 1
            record.last_attempt_at = datetime.now(timezone.utc)
            self.db.commit()
            before = self._snapshot(movie.id)
            if item.get("release_date") and item.get("platform") and item.get("source_url"):
                entry = CuratedRelease(
                    title=movie.title,
                    platform=normalize_platform(item["platform"]),
                    release_date=date.fromisoformat(item["release_date"]),
                    source_url=item["source_url"],
                    source_title=item.get("source_title") or f"Verified OTT release for {movie.title}",
                    language=movie.original_language,
                )
                self._save_entry(movie, entry, manual=True)
            after = self._snapshot(movie.id)
            report["platforms_found"] += int(bool(after["platforms"]))
            report["ott_dates_found"] += int(after["confirmed_date"])
            report["legal_urls_found"] += int(bool(after["urls"]))
            report["no_result"] += int(not after["confirmed_date"])
            record = self._record(movie.id)
            record.status = "DONE" if after["confirmed_date"] else "DEFERRED"
            record.last_error = None if after["confirmed_date"] else "NO_VERIFIED_OTT_DATE"
            self.db.commit()
        state = self._state()
        state.processed_count = self.db.query(func.count(BackfillRecord.id)).filter_by(operation=self.operation).scalar() or 0
        details = dict(state.details or {})
        details["quality_test"] = report
        state.details = details
        state.status = "IDLE"
        state.last_success_at = datetime.now(timezone.utc)
        self.db.commit()
        return report

    def run_batch(self, batch_size: int = 500, batch_number: int | None = None) -> dict:
        if batch_number is None:
            batch_number = int((self._state().details or {}).get("batches_completed") or 0) + 1
        targets = self.targets(batch_size)
        state = self._state()
        state.total_count = self.db.query(func.count(Movie.id)).filter(self._incomplete()).scalar() or 0
        state.status = "RUNNING"
        state.last_error = None
        self.db.commit()
        index = CuratedReleaseIndex()
        index.load({movie.original_language for movie in targets if movie.original_language})
        report = {
            "batch": batch_number, "processed": 0, "platforms_added": 0,
            "ott_dates_added": 0, "legal_urls_added": 0, "confirmed_existing": 0,
            "no_result": 0, "needs_review": 0, "source_failures": len(index.failures),
            "errors": 0,
        }
        for item_number, movie in enumerate(targets, 1):
            before = self._snapshot(movie.id)
            record = self._record(movie.id)
            record.status = "RUNNING"
            record.attempts = (record.attempts or 0) + 1
            record.last_attempt_at = datetime.now(timezone.utc)
            record.last_error = None
            self.db.commit()
            matched_sources: list[str] = []
            status = "NO_RESULT"
            try:
                for entry in index.matches(movie):
                    if self._save_entry(movie, entry):
                        matched_sources.append(entry.source_url)
                after = self._snapshot(movie.id)
                report["platforms_added"] += len(after["platforms"] - before["platforms"])
                report["ott_dates_added"] += len(after["dates"] - before["dates"])
                report["legal_urls_added"] += len(after["urls"] - before["urls"])
                if after["confirmed_date"]:
                    status = "CONFIRMED" if not before["confirmed_date"] else "CONFIRMED_EXISTING"
                    report["confirmed_existing"] += int(before["confirmed_date"])
                    record.status, record.last_error = "DONE", None
                elif matched_sources:
                    status = "NEEDS_REVIEW"
                    report["needs_review"] += 1
                    record.status, record.last_error = "DEFERRED", "SINGLE_SOURCE_OR_UNCONFIRMED_DATE"
                else:
                    report["no_result"] += 1
                    record.status, record.last_error = "DEFERRED", "NO_RESULT"
                state = self._state()
                state.cursor = movie.id
                state.processed_count = (state.processed_count or 0) + 1
                report["processed"] += 1
                self.db.commit()
                release = movie.theatrical_release_date or movie.release_date
                print(
                    f"BATCH: {batch_number} ITEM: {item_number}/{len(targets)} TITLE: {movie.title} "
                    f"YEAR: {release.year if release else ''} LANGUAGE: {movie.original_language or ''} "
                    f"PLATFORM_RESULT: {','.join(sorted(after['platforms'])) or 'NONE'} "
                    f"OTT_DATE_RESULT: {'CONFIRMED' if after['confirmed_date'] else 'NULL'} "
                    f"LEGAL_URL_RESULT: {'PRESENT' if after['urls'] else 'NULL'} "
                    f"SOURCE: {','.join(sorted({urlparse(url).hostname or '' for url in matched_sources})) or 'NONE'} STATUS: {status}",
                    flush=True,
                )
            except Exception as exc:
                self.db.rollback()
                record = self._record(movie.id)
                record.status = "FAILED"
                record.last_attempt_at = datetime.now(timezone.utc)
                record.last_error = sanitize_error(exc)
                report["errors"] += 1
                report["processed"] += 1
                state = self._state()
                state.cursor = movie.id
                state.processed_count = (state.processed_count or 0) + 1
                self.db.commit()
        state = self._state()
        details = dict(state.details or {})
        cumulative = dict(details.get("cumulative") or {})
        for key, value in report.items():
            if key != "batch":
                cumulative[key] = int(cumulative.get(key) or 0) + int(value)
        details.update({
            "batch_size": batch_size,
            "batches_completed": max(int(details.get("batches_completed") or 0), batch_number),
            "last_batch": report,
            "cumulative": cumulative,
            "source_failures": index.failures,
        })
        state.details = details
        state.last_success_at = datetime.now(timezone.utc)
        remaining = bool(self.targets(1))
        # A completed batch has no live runner between invocations.  Keep the
        # pending flag in details rather than leaving a stale RUNNING state.
        details["remaining_due"] = remaining
        state.details = details
        state.status = "IDLE"
        self.db.commit()
        return report | {"remaining_due": remaining, "cumulative": cumulative}
