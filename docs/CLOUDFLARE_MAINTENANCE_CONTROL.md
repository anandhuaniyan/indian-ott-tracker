# Cloudflare maintenance route control

This feature changes only the two configured Worker routes. It never uploads,
edits, or deletes a Worker script and never changes unrelated routes.

The existing Discord bot should register the administrator-only slash command
`/maintenance` with choices `on`, `off`, and `status`, then POST its interaction
to `/api/v1/maintenance/discord` with this JSON shape:

```json
{
  "interaction_id": "Discord interaction ID",
  "actor_id": "Discord user ID",
  "guild_id": "Discord guild ID",
  "action": "on"
}
```

Send the backend-only shared secret in `X-Discord-Maintenance-Secret`. The API
independently enforces `DISCORD_MAINTENANCE_ADMIN_IDS` and, when configured,
`DISCORD_MAINTENANCE_GUILD_ID`; Discord role names or client-supplied claims do
not grant access.

`on` creates only missing exact target routes for `otttracker-maintenance`.
`off` deletes only exact target routes currently assigned to that Worker.
Every mutation is serialized with Redis, re-read from Cloudflare, verified, and
written as a structured audit event. A target route assigned to another Worker
is a hard conflict and is not overwritten.

## Required Cloudflare token scope

Create a dedicated API token limited to the `otttracker.in` zone with:

- Zone — Workers Routes — Edit

No DNS, cache, firewall, account administration, or Worker Scripts write scope
is required. The service uses `CLOUDFLARE_ZONE_ID`; `CLOUDFLARE_ACCOUNT_ID` is
recorded for deployment inventory but is not sent by the route API client.

Before enabling commands, configure all backend-only values documented in
`.env.example`, verify `status`, then exercise `on` and `off` from a non-live
Cloudflare zone or a mocked/staging endpoint. Never place these secrets in a
`VITE_` variable.
