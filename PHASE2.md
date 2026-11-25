# Phase 2: platform Meta setup and customer-safe onboarding

This stage adds a platform-only `/platform-integrations` setup screen, alongside the `/organisations` customer-facing company connections page.

## Operator-only setup

The platform's legacy administrator logs into staging and opens `/platform-integrations`. It shows the safe, non-secret configuration status and precise OAuth callback URL. Credentials are never accepted by an application web form and must be stored as encrypted Railway service variables.

Staging callback: `https://arena-social-staging-staging.up.railway.app/api/integrations/meta/callback`.

The required variables are `META_APP_ID`, `META_APP_SECRET`, `META_TOKEN_ENCRYPTION_KEY` and `PUBLIC_ORIGIN`. The last two are already installed on staging; App ID/Secret must be supplied by the platform operator. Do not copy a production Meta App Secret into tickets, chat, or GitHub.

## Customer experience

Each member visits `/organisations` for the Pages of companies to which they belong. When no central Meta app is configured, the connection button is disabled and shows a friendly unavailability message; no API jargon or key requirements appear in the customer interface.

Once centrally configured, company owners/managers can begin Meta OAuth and select a Page. For this first version, Instagram is discovered through a linked Instagram professional account using Facebook Login, not an independent Instagram Login. Individual customer Meta apps are never needed.

## Security scope and caveats

- Platform configuration diagnostic endpoint `GET /api/platform/integrations/meta` is restricted to the legacy administrator and never returns any secret or App ID.
- Tenant availability endpoint `GET /api/organisations/{id}/meta/availability` requires membership and returns only readiness and a customer-safe message.
- Meta token data stays encrypted and tenant-scoped under `tenant_meta.py`.
- **Not yet commercial-safe:** existing global `/admin`, `/api/config`, display, event settings, staff accounts, cache and publication system are NOT fully tenant-isolated. No public customer onboarding until those are separated and security-reviewed.
- The central Meta app still needs the appropriate permissions, app roles for testing and App Review/Advanced Access for external customers. Credential presence alone does not prove approval.
- The `development` branch automatically deploys to the separate `Arena Social Staging` Railway project; `main` remains the source of the two existing production installations.

## Verification

Run `python -m unittest discover -s tests -v` and `python -m py_compile server.py tenant_meta.py meta_connect.py`. Test platform diagnostics access, missing credentials, tenant-specific availability, OAuth callback redirect, Page selection, and isolation.
