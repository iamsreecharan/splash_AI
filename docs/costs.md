# Start with zero service spend

The recommended starting point is the local Docker stack with
`SPLASH_AI_ENABLED=false` and the included fixtures. It requires no paid AI,
hosted database, Redis service, authentication account, or GitHub App. Your own
computer still supplies its hardware, storage, and electricity.

For a real local pilot, GitHub App setup and a Supabase Auth project's available
free allowance may be enough; quotas and provider terms must be checked in their
current dashboards. Keep PostgreSQL/pgvector and Redis local to avoid hosted
service costs. A webhook tunnel may have usage restrictions or paid features;
choose one deliberately.

OpenAI API use is opt-in and billed separately from ChatGPT. Keep it off until
structural analysis proves useful. When enabling it:

1. Use a separate API project and restricted key for Splash.
2. Choose the inexpensive model defaults and bounded source/context settings.
3. Start with a provider-side usage limit and alerts; confirm whether your
   account enforces a hard stop or only sends budget notifications.
4. Index only the test repository and analyze a few PRs first. Inspect API
   usage before widening access.
5. Turn `SPLASH_AI_ENABLED` back off to stop new hosted requests.

Per-request bounds reduce a single request's size; they are not a deployment
monthly dollar cap. Embeddings can add ingestion costs, and repeated indexing,
retries, or many PR updates can accumulate charges. Don't enable every optional
feature to test the free local workflow.

The optional hosted provider accepts these conservative defaults:

| Setting | Default |
| --- | --- |
| `SPLASH_AI_MAX_INPUT_TOKENS` | 6000 |
| `SPLASH_AI_MAX_OUTPUT_TOKENS` | 700 |
| `SPLASH_AI_MAX_SNIPPETS` | 8 |
| `SPLASH_AI_MAX_SNIPPET_CHARS` | 1800 |
| `SPLASH_AI_MAX_REQUEST_USD` | 0.02 estimated per request |
| `SPLASH_AI_MAX_TOTAL_USD` | 0.02 estimated per analysis job/provider instance |

These estimates use the provider module's reviewed prices. The app creates a
shared provider instance per analysis job, and actual provider billing is
authoritative. Use provider-side controls for account-wide spending. Unknown
model pricing should be reviewed before changing the default model.

The hosted path embeds only a bounded sample of source chunks and at most one
change query per analysis. Query embedding shares the job allowance with chunk
embedding and explanations. Semantic search covers that sample; local lexical
search remains available for the rest of the indexed project. AI stays off until
explicitly enabled, and provider-side controls remain necessary for a monthly
spending limit.

A reliable complete Render pilot includes an API, background worker, durable
PostgreSQL/pgvector, Redis-compatible queue, and static frontend. Free plans may
sleep, expire, restrict background processes, or lack needed persistence.
Treat Render as the later deployment target and verify its current prices,
quotas, and pgvector support first. No automatic paid resource provisioning is
included. The local configuration remains the default.
