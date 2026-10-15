# Splash V1 architecture and implementation contract

V1 is a local-first private pilot: React/TypeScript/Vite, FastAPI, PostgreSQL 16
with pgvector, Redis/Celery, and a controlled four-stage LangGraph workflow.
GitHub is read-only. Linear, Slack, chat, patches, and editor extensions belong to
later releases. No repository code is executed. Python and JavaScript/TypeScript
are the initial analysis targets.

The local sample workflow is free and deterministic. Hosted OpenAI reasoning and
embeddings are opt-in, bounded, and must not be necessary for the demo. Hosted
authentication uses Supabase. Demo mode is explicit and must be rejected for
public/production deployments. Secrets never reach browser bundles or logs.

## Shared HTTP contract

All authenticated routes live below `/api`. Production uses a Supabase bearer
token. Local demo uses an isolated seeded identity. Responses use JSON; lists use
`{items: [...]}`. Timestamps are ISO UTC strings, IDs are strings. HTTP failures
use FastAPI's `detail` property. The frontend API base is `/api` through Vite's
development proxy or the deployment reverse proxy.

- `GET /health`: health and dependency readiness.
- `GET /api/session`: `{user: {id, name, email}, organization: {id, name}, mode,
  capabilities: {github, ai, demo}}`.
- `GET /api/overview`: `{repositories, open_alerts, high_severity_alerts,
  analyzed_changes, recent_runs: [...], recent_alerts: [...]}`.
- `GET /api/repositories`: `{items: [Repository]}`.
- `POST /api/repositories/{id}/index`: 202 `{job_id, status}`.
- `GET /api/repositories/{id}/status`: `{id, status, indexed_files,
  indexed_chunks, last_indexed_at, error}`.
- `DELETE /api/repositories/{id}`: delete repository and associated indexed data,
  events, runs, alerts, feedback; preserve other tenants.
- `GET /api/alerts?severity=&status=&repository_id=`: `{items: [Alert]}`.
- `GET /api/alerts/{id}`: Alert including evidence and recommendations.
- `POST /api/alerts/{id}/feedback`: body `{kind: "useful"|"irrelevant"|"resolved"}`;
  return updated Alert. Resolved sets `status=resolved`, irrelevant sets
  `status=dismissed`, useful records feedback without resolving.
- `GET /api/runs?status=&repository_id=&q=&kind=&limit=&offset=`:
  `{items: [Run], total, limit, offset, has_more}`. Search matches a literal
  substring of the title, repository name, or PR number. Results sort by creation
  time, then ID, newest first. Counts and pages include only repositories the
  current user can still access. The default limit is 100; the history page
  requests 20 at a time.
- `GET /api/runs/{id}`: Run including stage trace.
- `GET /api/runs/{id}/report`: a Markdown attachment for a completed analysis.
  Includes its stages, findings, recommendations, and stored source excerpts.
  Queued, running, failed, and indexing runs return 409. Downloads recheck current
  repository access and use `Cache-Control: no-store`. Exporting doesn't call an
  AI provider or start a new analysis.
- `GET /api/integrations`: `{github: {configured, connected, app_name},
  openai: {configured, enabled, model}, supabase: {configured}, mode}`.
- `GET /api/github/install`: `{url}` or actionable 503 configuration detail.
- `POST /api/webhooks/github`: raw signed GitHub webhook; validate before enqueue,
  deduplicate by delivery ID. Never acknowledge failed enqueue as successful.
- `POST /api/demo/seed`: demo only; idempotently create the sample repository and
  index its base snapshot. Return `{repository_id, message}`.
- `POST /api/demo/analyze`: demo only; body `{scenario: "rename"|"safe"}`;
  return 202 `{job_id, status}`; process the genuine fixture through the workflow.

Repository: `{id, full_name, description, default_branch, language, status,
 indexed_files, indexed_chunks, last_indexed_at, html_url, is_demo}`. Status:
`pending|indexing|ready|failed`.

Alert: `{id, repository_id, repository_name, title, summary, severity,
 confidence, evidence_status, status, pr_number, pr_title, pr_url,
 affected_component, created_at, evidence: [Evidence], recommendations: [str],
 change_type, feedback: str|null}`. Severity: `high|medium|low`.
Evidence status: `verified|needs_investigation`. Status: `open|resolved|dismissed`.
Evidence: `{path, start_line, end_line, snippet, source_url, commit_sha, role}`.

Run: `{id, repository_id, repository_name, kind: "analyze"|"index", pr_number, title,
 status: "queued"|"running"|"completed"|"failed", created_at, completed_at,
 alert_count, error, stages: [{name, status, summary, duration_ms}]}`.

## Analysis module contract

`app.analysis.engine.analyze_change(*, repository_name, base_files, head_files,
 pr_number=1, title="", base_sha="", head_sha="") -> dict`

Snapshots are dictionaries mapping safe repository-relative paths to UTF-8 text.
Returns `{changes: [...], alerts: [...], stages: [...], metrics: {...}}`.
Alerts use the HTTP Alert fields without database IDs, timestamps, feedback,
repository_id or repository_name. Citations bind to an exact snapshot and SHA.
Verified alerts require an exact producer-to-consumer dependency; token matches
alone are insufficient. Harmless additions and formatting must not produce high
severity alerts. Uncertain relationships must be labelled explicitly.

`app.ingestion.indexer.index_files(files, repository_name="", commit_sha="")`
returns `{chunks: [{path, start_line, end_line, content, symbol, kind}],
 dependencies: [...]}`. Ignore credentials, binaries, generated/vendor files,
oversized files, and unsupported languages. Structural and lexical retrieval
must work with no AI credentials.

Backend and analysis code live in `backend/app`; frontend owns `frontend/`;
infra and operational docs own `infra/`, Dockerfiles, and setup guides. Pytest
tests belong in `backend/tests`, evaluation scenarios in `backend/evals`.
