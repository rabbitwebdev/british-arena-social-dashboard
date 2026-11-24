# British Arena Social Dashboard: Meta SaaS connection upgrade (integration package)

## Status

This is a **self-contained integration module**, NOT a deployed upgrade. It has NOT been merged into the private `rabbitwebdev/british-arena-social-dashboard` repository. GitHub access from this environment returned HTTP 404; therefore the existing Flask auth and database models could not be inspected and the module has intentionally not been deployed to either Railway installation.

## Customer experience

Authenticated customer clicks **Connect Facebook / Instagram** -> Meta consent -> Page(s) and linked Instagram business IDs are saved encrypted under their tenant -> customer sees connected accounts. They never create a Meta app or enter credentials. The SaaS operator must maintain ONE Meta Developer app and obtain the necessary permissions/Meta App Review. The module does not generate Meta App IDs or secrets (Meta does not expose a general supported API to do that for customer apps).

## Integration steps

1. Merge `meta_saas/` into the existing Flask application.
2. Install `requirements.txt` dependencies.
3. Implement `current_identity()` using your existing authenticated staff/company session, returning `(organisation_id, user_id)`. **Do not** derive organisation ID from an HTTP query/header parameter.
4. Call `register_meta_routes(app, current_identity)` after constructing the Flask app. This adds `GET /api/integrations/meta/connect`, `GET /api/integrations/meta/callback`, `GET /api/integrations/meta/connections`, and `DELETE /api/integrations/meta/connections/<page_id>`.
5. Add a button pointing to `/api/integrations/meta/connect` and render the `/connections` JSON. Protect all modifying endpoints with the app's existing CSRF middleware; `DELETE` includes an additional Origin check.
6. Add Railway variables to the **British Esports** service: `META_CONNECTION_DB=/data/meta-connections.sqlite3`, `META_APP_ID`, `META_APP_SECRET`, `META_TOKEN_ENCRYPTION_KEY`, `PUBLIC_ORIGIN=https://british-arena-social-dashboard-production.up.railway.app`. Preserve existing encryption key after rollout: replacing it makes encrypted credentials unreadable.
7. In Meta developer settings whitelist `https://british-arena-social-dashboard-production.up.railway.app/api/integrations/meta/callback`. **This is a new route**, not the current `/meta/callback`; keep the old route whitelisted until migration is complete.
8. Configure Meta Facebook Page + Instagram API with Facebook Login, obtain supported scopes (`pages_show_list`, `pages_read_engagement`, `instagram_basic`) and required App Review/Advanced Access before onboarding external customers. App roles can test before external approval. Verify Meta's current product/permission eligibility and verify the requested insight metrics separately.
9. First test in a development/staging service with fake data and a non-production Meta app. Verify authorization and rejection, page selection, one-time state, revoked tokens, login/session persistence, encryption key stability, company isolation, and account unlinking. Enable automatic deployments only after review.

## Important unfinished production features

- This proof-of-integration saves all granted Pages (up to 100) rather than providing an account-choice step. Add an explicit Page selection workflow before commercial release.
- Pagination is rejected safely when accounts have >100 Pages; implement pagination.
- A token lifecycle/background refresh and reauthorization job, Graph API rate limits/backoff, scheduled data sync, Instagram direct login, billing, audit logs, user roles, account deletion policy, secret rotation, consent/privacy controls and robust observability still need implementation.
- Do not treat every Page like/follower change as real-time; polling can show detected deltas only. The exact available metrics depend on the app's access and Meta's API version.
- The parent site's authentication, session cookie settings, CSRF middleware and database schema must be inspected before a production merge.

## Run storage unit tests

`python -m unittest discover -s tests -v` (Flask must be installed because importing the package currently imports its route module). For direct store-only tests see the local test output.
