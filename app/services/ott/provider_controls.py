"""Per-provider budget, cache, metrics, and circuit-breaker controls."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from time import monotonic

from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.secrets import sanitize_error
from app.models.ott_intelligence import OttProviderBudgetPeriod, OttProviderCache, OttProviderHealth
from app.services.ott.providers.base import ProviderDisabled, ProviderError, ProviderQuotaExhausted, ProviderRateLimited


def _aware(value):
    if value and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class OTTApiBudgetManager:
    def __init__(self, db: Session):
        self.db = db

    def _period(self, provider: str, period_type: str, key: str, limit: int, reset_at: datetime):
        row = (
            self.db.query(OttProviderBudgetPeriod)
            .filter_by(provider=provider, period_type=period_type, period_key=key)
            .with_for_update()
            .first()
        )
        if not row:
            row = OttProviderBudgetPeriod(
                provider=provider,
                period_type=period_type,
                period_key=key,
                request_limit=max(0, limit),
                reset_at=reset_at,
            )
            self.db.add(row)
            self.db.flush()
        elif row.request_limit != max(0, limit):
            row.request_limit = max(0, limit)
        return row

    def reserve(self, provider: str, daily_limit: int, monthly_limit: int, amount: int = 1) -> bool:
        now = datetime.now(timezone.utc)
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        if now.month == 12:
            next_month = now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            next_month = now.replace(month=now.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)
        day = self._period(provider, "DAY", now.date().isoformat(), daily_limit, tomorrow)
        month = self._period(provider, "MONTH", now.strftime("%Y-%m"), monthly_limit, next_month)
        if (day.request_limit and day.used + amount > day.request_limit) or (
            month.request_limit and month.used + amount > month.request_limit
        ):
            return False
        day.used += amount
        month.used += amount
        self.db.flush()
        return True

    def snapshot(self, provider: str) -> dict:
        now = datetime.now(timezone.utc)
        rows = self.db.query(OttProviderBudgetPeriod).filter(
            OttProviderBudgetPeriod.provider == provider,
            OttProviderBudgetPeriod.period_key.in_([now.date().isoformat(), now.strftime("%Y-%m")]),
        ).all()
        return {
            row.period_type.lower(): {
                "used": row.used,
                "limit": row.request_limit,
                "remaining": max(0, row.request_limit - row.used) if row.request_limit else None,
                "reset_at": row.reset_at,
            }
            for row in rows
        }


class OttProviderControlService:
    def __init__(self, db: Session):
        self.db = db
        self.budgets = OTTApiBudgetManager(db)

    def health(self, provider: str, *, enabled: bool) -> OttProviderHealth:
        row = self.db.query(OttProviderHealth).filter_by(provider=provider).first()
        if not row:
            row = OttProviderHealth(provider=provider, status="HEALTHY" if enabled else "DISABLED")
            self.db.add(row)
            self.db.flush()
        if not enabled:
            row.status = "DISABLED"
        return row

    def execute(self, provider, callback):
        health = self.health(provider.name, enabled=provider.enabled and provider.configured)
        if not provider.enabled or not provider.configured:
            self.db.commit()
            raise ProviderDisabled(f"{provider.name} is disabled or not configured")
        now = datetime.now(timezone.utc)
        circuit_until = _aware(health.circuit_open_until)
        if circuit_until and circuit_until > now:
            health.status = "DEGRADED"
            self.db.commit()
            raise ProviderError(f"{provider.name} circuit breaker is open")
        if not self.budgets.reserve(provider.name, provider.daily_limit, provider.monthly_limit):
            health.status = "QUOTA_EXHAUSTED"
            self.db.commit()
            raise ProviderQuotaExhausted(f"{provider.name} configured request budget is exhausted")
        health.request_count += 1
        started = monotonic()
        try:
            result = callback()
        except ProviderRateLimited as exc:
            self._failure(health, exc, "RATE_LIMITED", now, started)
            raise
        except ProviderQuotaExhausted as exc:
            self._failure(health, exc, "QUOTA_EXHAUSTED", now, started)
            raise
        except Exception as exc:
            self._failure(health, exc, "DOWN", now, started)
            raise
        health.status = "HEALTHY"
        health.consecutive_failures = 0
        health.last_success_at = now
        health.last_error = None
        health.circuit_open_until = None
        health.last_latency_ms = int((monotonic() - started) * 1000)
        health.success_count += 1
        health.match_count += len(result or [])
        self.db.commit()
        return result

    def _failure(self, health, exc, status, now, started):
        health.status = status
        health.consecutive_failures += 1
        health.last_failure_at = now
        health.last_error = sanitize_error(exc)
        health.last_latency_ms = int((monotonic() - started) * 1000)
        health.error_count += 1
        if health.consecutive_failures >= settings.OTT_PROVIDER_FAILURE_THRESHOLD:
            health.status = "DEGRADED" if status == "DOWN" else status
            health.circuit_open_until = now + timedelta(minutes=settings.OTT_PROVIDER_CIRCUIT_MINUTES)
        self.db.commit()

class OttProviderCacheService:
    def __init__(self, db: Session):
        self.db = db

    def get(self, provider: str, cache_key: str) -> dict | None:
        now = datetime.now(timezone.utc)
        row = self.db.query(OttProviderCache).filter_by(provider=provider, cache_key=cache_key).first()
        if row and _aware(row.expires_at) > now:
            return row.payload
        return None

    def put(self, provider: str, cache_key: str, payload: dict, ttl: timedelta) -> None:
        now = datetime.now(timezone.utc)
        row = self.db.query(OttProviderCache).filter_by(provider=provider, cache_key=cache_key).first()
        if not row:
            row = OttProviderCache(provider=provider, cache_key=cache_key, payload=payload, fetched_at=now, expires_at=now + ttl)
            self.db.add(row)
        else:
            row.payload = payload
            row.fetched_at = now
            row.expires_at = now + ttl
        self.db.commit()
