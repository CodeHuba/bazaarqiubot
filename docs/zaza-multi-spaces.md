# Zaza multi-space backend contract

Anonymous applications require `https://bazaardb.gg/run/profile/<UUID>` and a 3–48 character name ending in `空间`. Bare identifiers, `/profile/`, query strings and foreign hosts are rejected. The standalone worker fetches that canonical RSC page and requires structured matching profile identity plus a nickname. Optional verification/supporter flags remain null when upstream omits them.

Name and profile reservations cover pending, creating, active, failed and stopped. Rejected, cancelled and deleted release them. IDs remain stable across rename. Pending applications are publicly searchable but disclose no runs, summaries or card suggestions. Approval publishes after successful remote validation and local reconciliation; a valid empty local result becomes active. Remote profile failures retain existing published scope and store an operational error. Capture failures affect the per-run task only.

## Public API

- `POST /api/zaza/spaces`, JSON `{ "profileUrl": "https://bazaardb.gg/run/profile/<UUID>", "name": "名字空间" }`: 201 with `{space}`. Invalid input: 400; reservation conflict: 409.
- `GET /api/zaza/spaces?query=名字`: `{spaces:[{id,name,status,created_at,updated_at,current_season,current_phase,collection_error}]}`.
- `GET /api/zaza/spaces/<space_id>` and `/api/zaza/spaces/<space_id>/runs`: `{space,profile,scope,summary,pagination,suggestions,runs}`.
- `GET /api/zaza/spaces/<space_id>/runs/<run_key>/days`: `{run_key,snapshot_status,days:[{day,hour,time,outcome,items,skills}]}`. Only hour-6 PvP CombatStart snapshots are used; missing Days are not replaced with other hours or PvE. Unknown or unapproved runs return 404.

Detail query parameters: `page` (positive, default 1), `perPage` (1–100, default 20), exact `hero`, comma-separated or repeated `cards` with **AND** semantics, inclusive ISO `from`/`to`. `fromDate`/`toDate` and `start`/`end` are aliases. Invalid pagination/time values return 400. Dates should include an explicit UTC offset; timezone-less values are interpreted as UTC.

`pagination` has `page`, `per_page`, `total` (filtered count). `summary` describes the complete visible scope: `run_count`, `average_wins`, `ten_win_rate`, `heroes` (hero→count). `suggestions` is the deduplicated final-item display list for that scope. `profile` has `url`, `nickname`, `verified`, `supporter`. Runs have opaque `run_key`, `hero`, `time`, `wins`, `losses`, final `items`, final `skills`, `day_count`, `snapshot_status`. Items use the supplied art/name/size resolver. Skill resolvers may expose `skill_display_info_cached` or `resolve_skill`; raw skill titles are retained when unavailable. Native art dimensions/ratio belong to the frontend display.

## Admin API

All admin routes use the supplied existing Basic-auth authorization callback.

- `GET /api/admin/zaza/spaces`: `{spaces}` with operational fields.
- `PUT /api/admin/zaza/spaces/<space_id>`, JSON `{action,name?}`: `{space}`. Actions: approve/reject pending; retry active/failed; resume stopped; stop creating/active/failed; cancel pending/creating/failed; rename any reserved state; delete any nondeleted state. Invalid transitions return 409; missing IDs return 404.
- `GET /api/admin/zaza/settings`: `{settings:{auto_approve:false}}`.
- `PUT /api/admin/zaza/settings`, JSON `{auto_approve:true}` (alias `autoApprove`): same response. Strict boolean validation. Changes persist and affect future submissions only; existing pending applications remain pending. Manual review is the database default.
- `GET /api/admin/zaza/audit?space_id=<optional>`: `{audit:[{id,space_id,action,before_json,after_json,created_at}]}` newest first, capped at 200. Creation, lifecycle actions, rename and settings are audited in their write transaction.

## Storage and worker

`zaza_spaces.db` holds lifecycle, settings, audit, discovery outbox, run subscriptions, and a secondary local identity index. All lifecycle/outbox changes use `BEGIN IMMEDIATE`. Discovery has per-claim tokens, lifecycle generation checks and ten-minute stale-lease recovery. Initial discovery is immediate; recurring discovery and discovery retries are scheduled at the next Beijing odd-hour boundary. Phase changes trigger reconciliation immediately; historical subscriptions remain stored inactive.

The space API and worker open the runs database read-only. Changed DB/WAL fingerprints trigger a rebuild of profile/run identities into the Zaza database. Requests probe indexed subscription run IDs and recheck exact profile, season, phase and the configured collection time window. Active views require active subscriptions. The worker persists the collection window with profile metadata so stopped spaces keep their accepted scope across phase changes.

The staging browser collector separately writes an isolated runs database through authenticated lease-based APIs. Initial collection follows the newest/descending cursor until a short page or a row older than the authoritative phase start; subsequent collection reads the newest page. Upstream date filtering is not assumed: rows are filtered locally before upload. A successful empty collection is a completed sync. Ingest accepts native BazaarDB `statWins`/`statLosses` and the supported normalized aliases. Staging supplies `ZAZA_PHASE_START` and optional `ZAZA_PHASE_END` to both the API and worker; the lower bound is inclusive and the upper bound exclusive.

The snapshot database holds global facts and the separate `zaza_snapshot_jobs`/`zaza_combat_snapshots` tables. Zaza reuses matching global hour-6 PvP facts and waits for nonterminal global jobs; it never claims or updates global jobs. Terminal global results without suitable facts allow independent Zaza capture. Zaza task keys include run, season and phase, and claims require an active current-phase subscription. Tracker capture uses `/run/tracker/<run_id>` RSC, resolving JSON references and byte-length-prefixed UTF-8 text rows. Valid tracker responses without eligible facts become missing; malformed responses fail/retry. Full queue coexistence rules are in `zaza-snapshot-scope.md`.

Stop/delete invalidate discovery generations and disable subscriptions. Before committing capture, the worker checks global pause and current active scope again while holding the lifecycle database lock. Stopped details use the frozen season/phase and subscription set; snapshot facts fetched after `stopped_at` are excluded, including after rename. Resume restores collection into the latest scope. Shared facts and attempts are retained.

Run separately from Flask:

```bash
ENABLE_DAY_SNAPSHOT_QUEUE=1 python -m tools.zaza_space_worker --interval 60
```

Options: `--once`, `--runs-db`, `--spaces-db`, `--snapshots-db`, `--season`, `--phase`. Defaults match the application DB environment variables and `data_client` season/phase constants. The process does not import Flask application startup. Optional `ZAZA_BAZAAR_COOKIES_JSON` supplies exported authenticated browser cookies to curl_cffi when tracker access requires them. Upstream challenge/error responses are recorded as failures with real backoff.

`ENABLE_DAY_SNAPSHOT_QUEUE=0` prevents discovery, admission and capture. Restart the independent worker after deployment/configured phase changes, as with other services using imported phase constants. No route/mining algorithm is introduced.

## Verification

Focused tests cover canonical URL validation, pending non-disclosure, identity/scope rejection, AND item filters, pagination/time validation, persisted settings/audit, invalid transitions, stale generations/leases, backoff, repeated discovery success, actual RSC parser storage, paused/stopped collection, current-phase switching and shared fact reuse.

Live upstream acceptance was exercised with profile `0b180fb3-7fb9-430d-85c8-019b3658d8c7` (`qqr`) and an isolated local fixture containing one actual upstream run. The worker captured 15 real Days for `baf53d51-4586-4bfc-9ced-f73821eaf2b9`. Local HTTP submit, approve, detail and Day requests returned 201/200/200/200. This verification fixture is temporary and is not a production run import.
