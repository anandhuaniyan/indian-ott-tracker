# Indian OTT Tracker — Deployment Notes

Public site: **https://otttracker.in** (www.otttracker.in redirects here)

Last verified: 2026-09-06

## Architecture

```
Internet
  └─ https://otttracker.in  (Cloudflare edge, proxied)
       └─ Cloudflare Tunnel "otttracker" (id 0dd4ef6e-9f08-4d8c-b4d4-35dd820f8c37)
            └─ container: indian_ott_cloudflared  (cloudflare/cloudflared:2026.8.3)
                 └─ http://frontend:80  (container: indian-ott-tracker-frontend-1, nginx:1.27)
                      ├─ /api/   -> http://api:8000   (indian_ott_api)
                      ├─ /media/ -> http://api:8000
                      ├─ /storage/ -> http://api:8000
                      ├─ robots.txt / sitemap.xml / ads.txt -> http://api:8000
                      └─ all other paths -> SPA index.html (try_files)
```

- Backend: FastAPI (`app/`), Postgres 16, Redis 7, Celery worker + beat.
- All containers on one Docker network: `indian_ott_network`.
- Single compose file: `docker-compose.yml` (compose project `indian-ott-tracker`).

## Cloudflare Tunnel

- Name: `otttracker` (id `0dd4ef6e-9f08-4d8c-b4d4-35dd820f8c37`)
- Config: `C:\Users\anadh\.cloudflared\config.yml`
  - `otttracker.in`   -> `http://frontend:80`
  - `www.otttracker.in` -> `http://frontend:80`
  - fallback -> `http_status:404`
  - `originRequest.httpHostHeader` set per hostname.
- Credentials: `C:\Users\anadh\.cloudflared\0dd4ef6e-9f08-4d8c-b4d4-35dd820f8c37.json`
- Account auth: `C:\Users\anadh\.cloudflared\cert.pem` (legacy "ARGO TUNNEL TOKEN" format —
  NOT a TLS client certificate; used only by cloudflared CLI/daemon).
- Windows CLI binary: `C:\Users\anadh\.cloudflared\bin\cloudflared.exe`
  (e.g. `cloudflared tunnel list / info otttracker`).

## DNS (Cloudflare zone: otttracker.in)

- `otttracker.in` — proxied (orange cloud), resolves to Cloudflare edge IPs.
- `www.otttracker.in` — proxied, resolves to Cloudflare edge IPs.
- www detail: the origin nginx returns `308 -> https://otttracker.in` for host
  `www.otttracker.in`; SSL/TLS terminates at the Cloudflare edge.

## Ports / exposure (security)

| Port | Bind | Service | Publicly reachable? |
|------|------|---------|---------------------|
| 5173 | 0.0.0.0 | frontend (nginx) | LAN yes; internet via tunnel only |
| 8000 | 127.0.0.1 | backend API | No (loopback only) |
| 5433 | 127.0.0.1 | Postgres | No |
| 6380 | 127.0.0.1 | Redis | No |
| 5050 | 127.0.0.1 | pgAdmin (profile "tools") | No |

No inbound router/NAT opening is required — the tunnel makes a single outbound
connection to Cloudflare.

## Reboot / restart persistence

- Docker Desktop: `AutoStart: true` (settings-store.json) + HKCU\...\Run entry
  "Docker Desktop" -> starts at login.
- All ott container services: `restart: unless-stopped`.
- After a Windows reboot + login the whole stack restarts automatically,
  including the tunnel.
- Verified: `docker restart indian_ott_cloudflared` -> 4 new edge connections
  (sin02/sin08/sin15/sin20), public site + API return 200 afterwards.
- Caveat: Docker Desktop requires an interactive login session (no headless boot).
- Note: the `cloudflared` compose service is under `profiles: ["tunnel"]`.
  A plain `docker compose up -d` will NOT (re)create it — run
  `docker compose --profile tunnel up -d` if the stack is ever brought down.

## Verified results (2026-09-06)

| Check | Result |
|-------|--------|
| https://otttracker.in | 200 OK |
| https://www.otttracker.in | 308 -> https://otttracker.in/ |
| https://otttracker.in/api/v1/home | 200, ~88KB JSON (live movie data) |
| /movies /discover /search /ott /calendar/upcoming | 200 |
| /robots.txt /sitemap.xml /ads.txt | 200 |
| Container health | api/frontend/worker/beat/postgres/redis healthy; cloudflared connected |

## Operational notes

- Fronend env (build args): `VITE_API_URL` empty -> relative `/api` proxied by nginx;
  `VITE_SITE_URL=https://otttracker.in`; backend `FRONTEND_ORIGINS` includes
  `https://otttracker.in`, `https://www.otttracker.in`, localhost.
- Frontend image build: `./frontend/Dockerfile` (node build -> nginx). Rebuild with
  `docker compose build frontend && docker compose up -d frontend`.
- Backend healthcheck route: `/health`; public admin API under `/api/v1/admin`.
- Database/state live in named volumes `ott_postgres_data` (Postgres) and
  `ott_media_data` (media). Never delete these.
- Backend secrets live in `.env` (git-ignored). Keep them secret.