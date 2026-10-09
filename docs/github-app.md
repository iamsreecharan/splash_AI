# Read-only GitHub App

The GitHub App is separate from the application repository. Creating one and
installing it requires your own GitHub account and authorization. Do not share
its secret values in chat.

## Create the App

In GitHub Settings → Developer settings → GitHub Apps, create an App for the
private pilot. Use a descriptive unique name and set the home URL to your web
application. Configure these URLs to match your environment:

| Setting | Local development example |
| --- | --- |
| OAuth callback URL | `http://localhost:8000/api/github/oauth/callback` |
| Setup URL after installation | `http://localhost:8000/api/github/install/callback` |
| Webhook URL | `https://YOUR-TUNNEL/api/webhooks/github` |
| Homepage | `http://localhost:5173` |

Grant repository **Contents: read**, **Pull requests: read**, and the mandatory
**Metadata: read**. No write permissions are needed. Subscribe to **Pull
request**, **Push**, **Installation**, and **Installation repositories** events.
Only install it on repositories you intend to index. Use a separate GitHub
OAuth App for Supabase's GitHub login provider if you choose that login option;
do not confuse its callback with the Splash GitHub App callback.

Enable the App's **Redirect on update** option so reconnecting an existing
installation reaches the Setup URL callback after its settings are confirmed.
The V1 reconnect flow visits the installation configuration again and depends
on that callback to finish linking the renewed user authorization.

Generate a webhook secret and App private key. Save the following names in the
API and worker's private environment configuration:

```text
SPLASH_GITHUB_APP_ID
SPLASH_GITHUB_APP_SLUG
SPLASH_GITHUB_CLIENT_ID
SPLASH_GITHUB_CLIENT_SECRET
SPLASH_GITHUB_WEBHOOK_SECRET
SPLASH_TOKEN_ENCRYPTION_KEY
```

For Compose, place the downloaded PEM at `.secrets/github-app.pem`, keep it mode
600, and set `SPLASH_GITHUB_PRIVATE_KEY_PATH=/run/splash-secrets/github-app.pem`
in the private `.env`. `make up` runs the application as your host UID/GID so it
can read the mode-600 mount without opening permissions. If using Compose
directly, set `SPLASH_RUNTIME_UID` and `SPLASH_RUNTIME_GID` to your host IDs.
For hosting, use the platform's secret file and its absolute path instead. The
optional `SPLASH_GITHUB_PRIVATE_KEY` PEM environment setting is an alternative,
not a requirement to copy the key into a source file.

Generate a Fernet encryption key into the secret storage you control, for
example through a private password-manager entry using Python cryptography's
`Fernet.generate_key()`. Set it once for API and worker. It protects stored
GitHub user tokens. Changing it without migrating the encrypted tokens makes
existing links unusable; retain a secure backup and relink users after an
intentional reset. Never put the key or token in a browser variable.

Splash retains the linked GitHub user's token encrypted and intersects that
user's repository access with the App's installation access on subsequent
reads. V1 does not refresh expiring GitHub user tokens. If your App enables
GitHub's default expiring user-token behavior (typically eight hours), use
Splash's connection flow again when it expires. Access fails closed until
reconnection; installing the App does not make a revoked user permission valid.

Set `SPLASH_DEMO_MODE=false`. Ensure `SPLASH_PUBLIC_API_URL`, `SPLASH_WEB_URL`,
and `SPLASH_CORS_ORIGINS` match your exact origins. Restart API and worker after
configuration changes. The browser reads safe capabilities from the API;
installing an App alone does not authenticate a user or give them ownership of
an installation. Sign in with Supabase, then link GitHub through Splash's
state-checked authorization flow. Repository selection must use installations
visible to the linked user.

For a hosted pilot, use a single public HTTPS origin that routes `/api/*` to the
backend and other paths to the web app. Set `SPLASH_PUBLIC_API_URL` to that
public origin too, and register both callback paths there. The authorization
flow uses a secure host-only state cookie; callbacks on a different hostname
will lose it. A frontend/API pair on unrelated hosts does not become safe or
functional merely by allowing CORS. Local `localhost` ports share a hostname.
Use `localhost` consistently for the browser, public API URL, and callback URLs;
mixing it with `127.0.0.1` loses the host-only state cookie even on one computer.

## Local webhook tunnel

GitHub cannot call a loopback address. Use a HTTPS tunnel you control that
forwards only the webhook path to port 8000, or configure equivalent routing.
Keep demo mode off and do not tunnel PostgreSQL or Redis. Publicly tunneling an
unrestricted development server also makes its other routes reachable, so
prefer an ingress rule permitting the webhook path alone. Configure the final
URL and the same webhook secret in GitHub and Splash. Never skip signature
verification to troubleshoot a tunnel.

## Prove the live workflow

1. Sign in, link GitHub, install the App on a test Python/TypeScript repository,
   and select it. Confirm indexing completes and has nonzero chunks.
2. Open a PR that renames a FastAPI JSON response key (for example
   `machine_speed` to `speed`) without updating a TypeScript `fetch` consumer.
   Confirm GitHub's delivery page returns success, the background run completes,
   and Splash presents the affected caller and immutable commit links.
3. Send GitHub's redelivery for the same delivery ID. Confirm it does not create
   duplicate analysis or alerts.
4. Update the PR with a fix, then make a harmless formatting change. Confirm the
   new commit is analyzed and the harmless change adds no high-severity alert.
5. Use feedback; reload to confirm it persists. Remove the repository in Splash
   and confirm its index, alerts, and analysis history disappear.
6. Remove the installation's repository permission in GitHub. Confirm Splash
   cannot continue fetching that repository.

Mocked tests and the local fixture cover the application paths; they do not
replace this live account-and-webhook check. GitHub API access uses
`api.github.com`; an isolated managed machine may need that hostname allowed in
addition to its existing Git HTTPS access.
