"""Cloudflare maintenance routing, Discord authorization, and crawler policy."""

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.v1 import cloudflare_maintenance as api_module
from app.api.v1.render import BOT_UA_PATTERN
from app.config.settings import settings
from app.main import app
from app.services.cloudflare_maintenance import (
    CloudflareMaintenanceService,
    MaintenanceBusyError,
    MaintenanceControlError,
)


class FakeRedis:
    def __init__(self, acquired=True):
        self.acquired = acquired
        self.token = None

    def set(self, _key, token, **_kwargs):
        if not self.acquired:
            return False
        self.token = token
        return True

    def eval(self, _script, _keys, _key, token):
        if token == self.token:
            self.token = None
            return 1
        return 0


def route(route_id, pattern, script):
    return {"id": route_id, "pattern": pattern, "script": script}


def response(result=None, *, success=True, status=200, errors=None):
    return httpx.Response(status, json={"success": success, "result": result, "errors": errors or []})


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(settings, "CLOUDFLARE_API_TOKEN", "cf-secret")
    monkeypatch.setattr(settings, "CLOUDFLARE_ZONE_ID", "zone-1")
    monkeypatch.setattr(settings, "CLOUDFLARE_MAINTENANCE_WORKER", "otttracker-maintenance")
    monkeypatch.setattr(settings, "CLOUDFLARE_MAINTENANCE_ROUTES", "otttracker.in/*,www.otttracker.in/*")


def service(handler, redis_client=None):
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.cloudflare.test")
    return CloudflareMaintenanceService(client=client, redis_client=redis_client or FakeRedis())


def test_status_off_parses_routes_and_preserves_unrelated():
    svc = service(lambda request: response([route("other", "api.example.com/*", "other-worker")]))
    result = svc.status()
    assert result["state"] == "OFF"
    assert result["active_routes"] == []


def test_status_on_when_both_exact_routes_match():
    routes = [route("1", "otttracker.in/*", "otttracker-maintenance"), route("2", "www.otttracker.in/*", "otttracker-maintenance")]
    assert service(lambda request: response(routes)).status()["state"] == "ON"


def test_status_partial_is_reported():
    routes = [route("1", "otttracker.in/*", "otttracker-maintenance")]
    assert service(lambda request: response(routes)).status()["state"] == "PARTIAL"


def test_status_conflict_is_reported():
    routes = [route("1", "otttracker.in/*", "different-worker")]
    result = service(lambda request: response(routes)).status()
    assert result["state"] == "CONFLICT"
    assert result["conflicting_routes"] == ["otttracker.in/*"]


def test_enable_is_idempotent_when_already_on():
    calls = []
    routes = [route("1", "otttracker.in/*", "otttracker-maintenance"), route("2", "www.otttracker.in/*", "otttracker-maintenance")]

    def handler(request):
        calls.append(request.method)
        return response(routes)
    assert service(handler).change("on", "test-admin")["state"] == "ON"
    assert calls == ["GET", "GET"]


def test_enable_adds_only_missing_target_and_verifies():
    calls = []
    snapshots = [
        [route("1", "otttracker.in/*", "otttracker-maintenance"), route("x", "elsewhere/*", "other")],
        [route("1", "otttracker.in/*", "otttracker-maintenance"), route("2", "www.otttracker.in/*", "otttracker-maintenance"), route("x", "elsewhere/*", "other")],
    ]

    def handler(request):
        calls.append((request.method, request.url.path))
        return response(snapshots.pop(0) if request.method == "GET" else {"id": "2"})
    assert service(handler).change("on", "test-admin")["state"] == "ON"
    assert [method for method, _ in calls] == ["GET", "POST", "GET"]


def test_enable_conflict_fails_before_mutation():
    calls = []

    def handler(request):
        calls.append(request.method)
        return response([route("1", "otttracker.in/*", "different-worker")])
    with pytest.raises(MaintenanceControlError, match="another Worker"):
        service(handler).change("on", "test-admin")
    assert calls == ["GET"]


def test_disable_deletes_only_matching_target_routes_and_verifies():
    calls = []
    before = [
        route("1", "otttracker.in/*", "otttracker-maintenance"),
        route("2", "www.otttracker.in/*", "otttracker-maintenance"),
        route("x", "elsewhere/*", "other"),
    ]
    get_count = 0

    def handler(request):
        nonlocal get_count
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            get_count += 1
            return response(before if get_count < 3 else [before[-1]])
        return response(None)
    assert service(handler).change("off", "test-admin")["state"] == "OFF"
    deleted = [path for method, path in calls if method == "DELETE"]
    assert deleted == ["/zones/zone-1/workers/routes/1", "/zones/zone-1/workers/routes/2"]


def test_cloudflare_error_is_sanitized():
    def handler(_request):
        return response(None, success=False, status=403, errors=[{"message": "token cf-secret rejected"}])
    with pytest.raises(MaintenanceControlError) as caught:
        service(handler).status()
    assert "cf-secret" not in str(caught.value)


def test_invalid_cloudflare_payload_fails_closed():
    svc = service(lambda request: response({"not": "a list"}))
    with pytest.raises(MaintenanceControlError, match="route list"):
        svc.status()


def test_lock_contention_rejects_change():
    svc = service(lambda request: response([]), FakeRedis(acquired=False))
    with pytest.raises(MaintenanceBusyError):
        svc.change("on", "test-admin")


def test_missing_configuration_fails_without_request(monkeypatch):
    monkeypatch.setattr(settings, "CLOUDFLARE_API_TOKEN", "")
    with pytest.raises(MaintenanceControlError, match="CLOUDFLARE_API_TOKEN"):
        service(lambda request: pytest.fail("request must not be sent")).status()


def test_audit_log_contains_required_fields_without_secret(caplog):
    routes = [route("1", "otttracker.in/*", "otttracker-maintenance"), route("2", "www.otttracker.in/*", "otttracker-maintenance")]
    with caplog.at_level("INFO", logger="otttracker.maintenance.audit"):
        service(lambda request: response(routes)).change("on", "discord:42")
    audit = json.loads(caplog.records[-1].message)
    assert {"timestamp", "user", "action", "previous_state", "result", "success"} <= audit.keys()
    assert "cf-secret" not in caplog.text


def test_discord_authorization_allowlist_and_guild(monkeypatch):
    monkeypatch.setattr(settings, "DISCORD_MAINTENANCE_SHARED_SECRET", "shared")
    monkeypatch.setattr(settings, "DISCORD_MAINTENANCE_ADMIN_IDS", "42")
    monkeypatch.setattr(settings, "DISCORD_MAINTENANCE_GUILD_ID", "guild-1")
    monkeypatch.setattr(api_module.CloudflareMaintenanceService, "status", lambda self: {"state": "OFF"})
    client = TestClient(app)
    payload = {"interaction_id": "i-1", "actor_id": "42", "guild_id": "guild-1", "action": "status"}
    assert client.post("/api/v1/maintenance/discord", json=payload).status_code == 403
    headers = {"X-Discord-Maintenance-Secret": "shared"}
    assert client.post("/api/v1/maintenance/discord", json={**payload, "actor_id": "99"}, headers=headers).status_code == 403
    assert client.post("/api/v1/maintenance/discord", json={**payload, "guild_id": "wrong"}, headers=headers).status_code == 403
    accepted = client.post("/api/v1/maintenance/discord", json=payload, headers=headers)
    assert accepted.status_code == 200
    assert accepted.json()["state"] == "OFF"


@pytest.mark.parametrize("action", ["on", "off"])
def test_discord_mutation_commands_are_wired_without_cloudflare(monkeypatch, action):
    monkeypatch.setattr(settings, "DISCORD_MAINTENANCE_SHARED_SECRET", "shared")
    monkeypatch.setattr(settings, "DISCORD_MAINTENANCE_ADMIN_IDS", "42")
    monkeypatch.setattr(settings, "DISCORD_MAINTENANCE_GUILD_ID", "guild-1")
    calls = []

    def fake_change(_service, received_action, actor):
        calls.append((received_action, actor))
        return {"state": received_action.upper()}

    monkeypatch.setattr(api_module.CloudflareMaintenanceService, "change", fake_change)
    response = TestClient(app).post(
        "/api/v1/maintenance/discord",
        json={
            "interaction_id": f"interaction-{action}",
            "actor_id": "42",
            "guild_id": "guild-1",
            "action": action,
        },
        headers={"X-Discord-Maintenance-Secret": "shared"},
    )
    assert response.status_code == 200
    assert calls == [(action, "discord:42")]


@pytest.mark.parametrize("agent", ["ShapBot/1.0", "Mozilla/5.0 compatible; ShapBot"])
def test_shapbot_is_a_crawler_in_backend_and_nginx(agent):
    assert BOT_UA_PATTERN.search(agent)
    nginx = (Path(__file__).parents[1] / "frontend" / "nginx.conf").read_text(encoding="utf-8")
    assert "shapbot" in nginx.casefold()


def test_googlebot_and_ahrefs_remain_crawlers_while_browser_is_not():
    assert BOT_UA_PATTERN.search("Googlebot/2.1")
    assert BOT_UA_PATTERN.search("AhrefsBot/7.0")
    assert BOT_UA_PATTERN.search("Mozilla/5.0 Chrome/140.0") is None
