# V1 validation and remaining setup

The local development workflow was exercised in the cloud workspace using the
actual six-service Docker stack: API, worker, recovery scheduler, web app,
PostgreSQL 16 with pgvector, and Redis. The recovery scheduler dispatched tasks
that the worker completed; readiness was also checked through functional jobs.

| Check | Result |
| --- | --- |
| Python backend, analysis, retrieval, authorization, OAuth and queue tests | 166 passed |
| Frontend API handling and account isolation tests | 5 passed |
| TypeScript/production frontend build | Passed |
| Browser evidence/feedback, harmless change and mobile navigation | 3 passed |
| Docker workflow, exact source excerpts, feedback and erasure | 14 passed |
| PostgreSQL vector storage, cosine retrieval and tenant scope | 3 passed with synthetic vectors; transaction rolled back |
| Controlled impact evaluation | 74 of 74 scenarios passed |
| Reusable installation script | Executed successfully with hash/frozen-lock installation |

The evaluation contains 34 expected impact findings and checks 133 evidence
items against exact source lines and revisions. Precision, recall, and evidence
faithfulness are each 1.0 **on this synthetic dataset**. These measurements are
not general accuracy estimates for arbitrary repositories. See
[evaluation details](../backend/evals/README.md) and
[the machine-readable report](../backend/evals/report.json).

Initial deterministic detection covers supported literal FastAPI endpoints and
response shapes consumed through proven JavaScript/TypeScript fetch/json
bindings. Wrappers, dynamic URLs, ORM-driven schemas, inter-repository effects,
database changes, and arbitrary function/dependency upgrades are not comprehensively
analyzed. Unknown relationships stay unverified or unalerted; they do not become
confident warnings. Contextual source and Markdown retrieval is functional,
and optional semantic hints use only a small bounded sample of embedded chunks.

## What still needs account configuration

The free local fixture workflow requires no third-party accounts or API spend.
Live private-repository acceptance still needs:

1. A Supabase Auth project with asymmetric JWT signing and the correct redirects.
2. A read-only GitHub App, its private key, OAuth credentials, webhook secret,
   and a persistent token encryption key.
3. A reachable webhook/OAuth callback for local GitHub integration, using a
   securely configured tunnel and the same hostname for browser state cookies.
4. Optionally, a dedicated OpenAI API project/key and explicit AI opt-in.

Setup instructions are in [Supabase Auth](supabase-auth.md),
[GitHub App](github-app.md), [privacy](privacy.md), and [costs](costs.md).
Real hosted authentication, real GitHub OAuth/webhooks, and real OpenAI calls
were not exercised without those account settings. Authorization, revocation,
signed webhook replay, and provider behavior were tested with controlled mocks.
No hosted deployment has been validated.

The cloud environment draft adds `api.github.com` and `api.openai.com` to its
custom network domains and saves complete installation/startup instructions.
Review and save that draft in environment settings, then publish it to activate
the reusable configuration. Add your exact Supabase project hostname when
configuring hosted authentication. Draft saving does not activate networking,
publish a snapshot, or establish readiness in a fresh task.
