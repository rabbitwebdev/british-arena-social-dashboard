# Phase 3: tenant-specific screen editing, publishing and follower sync

Development branch only. Railway staging auto-deploys from `development`, while the two existing production dashboards remain pinned to `main`.

## Getting started on staging

1. Sign in at `https://arena-social-staging-staging.up.railway.app/login`.
2. Go to `/organisations`, then click **Manage dashboard & TV screen** below a company.
3. Set the company title, text, colours, image links, follower celebrations and promotional slides.
4. Select **Save draft** to store un-published settings. Select **Publish to TV** to update the TV content.
5. Under **TV screen access**, an organisation owner selects **Generate / replace display link**. Copy the URL immediately. The opaque link is only returned once; replacing it revokes the previous one.
6. Open the private display link on a TV or Raspberry Pi in kiosk mode. The screen checks for new published settings and follower counts every 15 seconds.
7. Connect the company's Facebook Page through `/organisations` (existing Meta OAuth). A background poll checks connected account counts approximately every 90 seconds.

## Multi-company boundaries

- `tenant_screens.py` stores draft and published settings keyed by organisation ID; another company never receives this state.
- TV viewing uses a random unguessable token hashed in SQLite. Only the matching company's *published* settings and aggregate counts are returned.
- Staff access requires explicit organisation membership. Editors need owner/manager access; only owners may rotate public screen links.
- Shared British Arena `/admin`, `/api/config`, `/display` and publishing are legacy routes. The `/admin` and global configuration/write APIs are now restricted to British Arena members. New staff are not silently added to British Arena.
- Meta credentials remain encrypted per organisation. Followers are polled, not real-time individual follow events; celebration effects occur when counts increase between polls.
- The screen shows the latest-connected Facebook Page for each company. Multiple Pages can currently be linked, but follower sync only uses the most recently connected Page. A Page selection/aggregation option remains a follow-up feature.

## API

- `GET /api/organisations/{id}/screen/config`: member draft.
- `GET /api/organisations/{id}/screen/published`: member published preview.
- `POST /api/organisations/{id}/screen/config`: owner/manager draft save.
- `POST /api/organisations/{id}/screen/publish`: owner/manager publish.
- `GET /api/organisations/{id}/screen/social`: member follower counts and connection health.
- `GET /api/organisations/{id}/screen/access`: screen-link status, no raw key.
- `POST /api/organisations/{id}/screen/rotate-key`: owner only; returns private URL once.
- `GET /api/screens/{opaque-token}`: private screen read-only feed; never returns Meta tokens or account IDs.
- `GET /screen/{opaque-token}`: screen HTML.

## Testing and safety

Run `python -m unittest discover -s tests -v` and `python -m py_compile server.py tenant_meta.py tenant_screens.py meta_connect.py` against the development checkout. Tests cover company separation, published-vs-draft isolation, screen key revocation, cross-account access checks, encrypted token use, failed sync recovery, and Meta permissions from earlier phases. Live Meta follower-count sync requires valid test Page data and has not been independently confirmed by automated tests.

**Not public launch-ready.** The original Arena screen and global app still coexist. Company screen UI includes a smaller subset of the legacy visual editor features. Company onboarding/invitations, tenant-scoped newsletter webhook events, subscription billing, audit logs, Meta token refresh/reconnect workflows, content security hardening and detailed GDPR data deletion must be completed and security-reviewed before onboarding unrelated paying customers.

## Operational caution

Do not merge `development` into `main` or copy staging variables/data to production as part of this phase.
