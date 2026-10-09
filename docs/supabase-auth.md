# Supabase Auth for the private pilot

Demo mode provides a local fixture identity only. For real repository access,
create a Supabase project and use Supabase Auth. Splash's indexed code remains
in its own PostgreSQL 16 database; an Auth project does not replace that database.

Set `SPLASH_SUPABASE_URL` and `SPLASH_SUPABASE_ANON_KEY` in the API environment,
then set `SPLASH_DEMO_MODE=false`. Use the project's public anon/publishable key,
**never a service-role key**. The API exposes only public Auth settings to the
browser. Session access tokens remain bearer credentials and must not be logged
or copied into URLs. The API verifies identity through the configured Auth
service and scopes data to the authenticated user/organization.

Use a Supabase JWT signing key with **ES256** or **RS256**. Splash verifies
asymmetric signatures against the project's public Auth JWKS endpoint, plus
issuer, audience, expiry, and authenticated role. Legacy **HS256** tokens are
rejected; migrate a legacy project through Supabase's supported signing-key
settings before using it. Do not give Splash a JWT signing secret or service-role
key as a workaround. New project defaults can differ, so check the active key.

In the Supabase Authentication settings, set the site URL to the exact web
origin, initially `http://localhost:5173`. Add your exact localhost redirect URL
and any later HTTPS production redirect URL. Enable the login method used by
the app (email/password, or GitHub provider if configured). Email confirmation and
Auth rate limits can affect a free pilot; inspect Supabase's Auth logs and follow
its current SMTP/provider guidance if real-user mail is not delivered.

For a private pilot, invite the intended users and restrict public signups in
Supabase according to your chosen login method. A publicly deployed UI is not
automatically a closed pilot. Confirm an uninvited identity cannot create a
session before sharing the URL.

If enabling Supabase's GitHub login provider, create the provider's OAuth
configuration with the Supabase callback URL shown in its dashboard. This
login config is separate from the read-only Splash GitHub App, which authorizes
repository access. Sign-in and installation alone do not prove that a user is
allowed to read an installation; complete Splash's GitHub linking flow.

Before live use, sign in, sign out, confirm unauthenticated API requests are
denied, and sign in with a second pilot identity to check its repository data is
separate. A managed cloud machine may need the exact project hostname
`YOUR-PROJECT.supabase.co` allowed for token verification. Do not add wildcard
access or supply Auth secret values in chat.
