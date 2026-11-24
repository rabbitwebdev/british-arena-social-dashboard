# Phase 1: tenant-aware Meta connections (development branch)

This is a **development-only integration** into the existing `server.py` stdlib HTTP server. There is no Flask migration, no change to `main`, and no Railway production deployment.

## What's integrated

- `tenant_meta.py` uses the application's existing `arena_session` login and staff records.
- Organisation creation and membership records in the existing SQLite database.
- Existing British Arena staff are assigned ONLY to the British Arena organisation.
- Company-scoped Meta OAuth state, encrypted temporary tokens, Page credentials and connection listing.
- Company-specific Page selection and disconnection; an account can be linked independently to more than one organisation.
- `/organisations` is the authenticated setup page, with company creation for legacy admin and self-service connection buttons for owners/managers.
- Built-in one-time authorization state and 10-minute expiry; POST operations require existing same-origin checks.
- Existing legacy `/api/meta/*` routes remain unchanged for backward compatibility.

## API routes

| Method | Route | Access |
| --- | --- | --- |
| GET | `/api/organisations` | Logged-in staff |
| POST | `/api/organisations` | Legacy admin |
| POST | `/api/organisations/{id}/members` | Organisation owner |
| GET | `/api/organisations/{id}/meta/connections` | Organisation member |
| GET | `/api/organisations/{id}/meta/connect` | Owner or manager |
| GET | `/api/integrations/meta/callback` | Authenticated OAuth callback |
| POST | `/api/organisations/{id}/meta/select` | Owner or manager |
| POST | `/api/organisations/{id}/meta/disconnect` | Owner or manager |

## Configuration and test

The existing Railway `META_APP_ID`, `META_APP_SECRET`, `META_TOKEN_ENCRYPTION_KEY`, `PUBLIC_ORIGIN`, `DATABASE_PATH`, `ADMIN_PASSWORD`, `SESSION_SECRET`, `DISPLAY_KEY` are reused. Never commit actual values or rotate the encryption key without migrating stored credentials.

**New Meta valid OAuth redirect URI:** `https://<your-staging-host>/api/integrations/meta/callback`.

Run `python -m unittest discover -s tests -v` and `python -m py_compile server.py tenant_meta.py meta_connect.py`. GitHub Actions has a development-only workflow: `.github/workflows/phase1-tests.yml`.

## Not production-ready

1. Existing app-wide dashboard config, screen/player routes, staff administration, follower cache and publications are still single-site. **They are not yet segregated per tenant.** Do not onboard external customers or expose them to this development instance until that separation is complete.
2. The system still uses the existing global admin and staff login, not a customer signup or invitation process. Company creation is restricted to the legacy administrator.
3. No automatic Page-token reauthorization/revocation job, customer self-service user provisioning, audit trail, GDPR deletion workflow, billing, Instagram direct login, Page pagination, or rate-limited per-company analytics sync.
4. One central Meta app must be configured and approved by Meta. Customers do **not** automatically receive newly generated App IDs; they authorize via OAuth.
5. Phase 1 has security unit tests, not verified live Meta integration tests. A separate staging environment and approved test account are needed before release.
6. The uploaded `meta_saas/` Flask proof-of-concept is **not used** by the active server; the integrated module is `tenant_meta.py`.

## Production safety

`main` is the deployment source for both existing Railway installations. Do not merge `development` into `main` until tenant isolation and tests are complete and deliberately approved.
