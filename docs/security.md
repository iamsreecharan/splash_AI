# Private-pilot security checklist

The local demo is intentionally convenient and accepts a fixture identity.
Bind it to loopback and keep `SPLASH_ENV=development`. Do not expose demo mode
through a public web server or tunnel. Production must set
`SPLASH_ENV=production`, `SPLASH_DEMO_MODE=false`, and configure real Supabase
authentication plus the read-only GitHub App. The application rejects a
production/demo combination.

Use HTTPS for a hosted web app, API, OAuth callbacks, and GitHub webhook ingress.
Use exact CORS origins, not a wildcard. Keep PostgreSQL and Redis on a private
network; the Compose localhost mappings are for local development only. Replace
the sample database password before hosted use. Render-managed TLS and private
service networking are deployment configuration, not something the demo proves.

Keep `.env`, `.secrets`, PEM files, encryption keys, database dumps, and logs
containing private code out of Git. Never put privileged keys in `VITE_*`.
The App private key and stored-token encryption key are independent secrets.
API and worker use the same encryption key. Back up that key securely, and
revoke/relink GitHub authorization tokens after a planned key rotation if you
have not implemented a data migration.

Uvicorn access logging is disabled in the provided startup commands so callback
query strings containing OAuth codes and state are not collected. Configure
the same query redaction or omission for your reverse proxy, webhook tunnel,
and hosting access logs before using live authorization.

Repository access requires authentication, tenant-scoped records, and verified
GitHub user/installation access. Installation IDs from a browser or unsigned
callback parameters are untrusted. Webhooks require the raw request's HMAC
signature and a unique delivery identifier before queueing. A signature failure
must never be worked around by disabling validation. Repeated delivery must
not create duplicate results. Uninstall/repository-removal events revoke access
and remove associated active source data.

Treat diffs, code, comments, and AI responses as untrusted content. Source is
parsed, not executed; generated HTML must be escaped. Hosted AI has no authority
to call tools or change repositories. Evidence links refer to exact snapshots;
uncertain findings remain labelled for investigation. Outputs still need human
review. The current private pilot is not a completed independent security audit.

Before admitting users, run the automated auth, signature, cross-tenant,
idempotency, input/path, and deletion checks, then perform the live account
checks in the GitHub and Supabase guides. Configure provider signup restrictions
for invited users. Add infrastructure ingress/rate limits and log/backup
retention suitable for your pilot; the local app should not be represented as
an unlimited public SaaS deployment.
