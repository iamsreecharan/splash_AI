# Later Render private pilot

Local Docker development is the default and avoids ongoing service charges.
Render is the intended hosted target when you are ready for real users. This
guide does not create resources. Check current plan limits and prices before
creating a worker or database; a reliable full stack cannot be promised free.

Provision these components on a shared private network where supported:

| Component | Configuration |
| --- | --- |
| API web service | Docker build `backend/Dockerfile`, context `backend`; `uvicorn app.main:app --host 0.0.0.0 --port $PORT --no-access-log` |
| Background worker | Same backend image; `celery -A app.tasks.celery_app worker --loglevel=info --concurrency=2` |
| Recovery scheduler | Same image; one `celery -A app.tasks.celery_app beat --loglevel=info --schedule=/tmp/splash-celerybeat` process |
| PostgreSQL | Version 16 with pgvector available; durable storage and backups |
| Redis-compatible queue | Durable queue service reachable by API/worker |
| Web frontend | Static site, `frontend`, `npm ci && npm run build`, publish `dist` |

Initialize the database by running `python -m app.bootstrap` once against the
deployment configuration before releasing the API and worker. Repeat execution
is safe for the current V1 schema. Future schema migrations need a controlled
migration path; table creation does not upgrade arbitrary existing schemas.
The PostgreSQL connection must use the SQLAlchemy psycopg dialect
(`postgresql+psycopg://…`) and any TLS requirement your host specifies. Confirm
the service supports `CREATE EXTENSION vector` or have an authorized database
administrator install it. A plain PostgreSQL service without pgvector is not
equivalent to the local image.

Use `SPLASH_ENV=production`, `SPLASH_DEMO_MODE=false`, and initially
`SPLASH_AI_ENABLED=false`. Set the database and Redis internal URLs, Supabase
public Auth configuration, all GitHub App credentials, and the persistent token
encryption key in private Render environment settings shared with the worker.
Mount the App private key through Render's secret-file mechanism and set its
exact file path. Never include `.env` in an image. Read the account setup guides
before configuring OAuth or App installation.

The browser's relative `/api` requests need a reverse proxy to the hosted API.
For a Render static site, define a rewrite of `/api/*` to the API's matching
`/api/*` path before the single-page fallback rewrite `/* → /index.html`.
If your current Render plan cannot proxy to the API this way, use a HTTPS ingress
with explicit same-origin routes instead of assuming a client-side fallback
will proxy requests. Use a single public HTTPS origin for the web app and
`/api/*`. Set both `SPLASH_WEB_URL` and `SPLASH_PUBLIC_API_URL` to that public
origin, and `SPLASH_CORS_ORIGINS` to its exact origin. GitHub OAuth state uses a
secure, host-only cookie: setting the callback to an unrelated direct API
hostname loses that cookie. Cross-site browser requests to an API on another
`onrender.com` host also cannot be assumed to accept that state cookie. Keep
the default same-origin `/api` base for this deployment. The Vite development
server is not the production web server.

Configure `/health` as the API health path. Check worker task completion and
queue connectivity separately: a healthy API cannot prove a functioning worker.
Update GitHub's OAuth callback and webhook URL, and Supabase's exact redirect
allowlist. Restrict Auth signup to intended pilot users. Add ingress rate limits
and a reviewed backup/retention policy for stored source and encrypted tokens.

Prove authentication, GitHub linking, repository authorization/indexing,
signature verification, a real PR analysis, immutable source links, feedback,
permission removal, and deletion before inviting more users. The local fixture
smoke test is not a deployed production acceptance test. Optional OpenAI calls
also require a live opted-in key, outbound HTTPS access, and provider budget
controls; leaving them off is valid.
