# British Arena Cloud Dashboard v12 (MVP)

This is the first centrally managed version of the existing v11 British Arena Social Dashboard, retaining the solid `#0B1F55` design, animation presets, sponsor/advert/event slides and Event Mode.

## Run locally on Mac (Python 3.9+)

1. Extract the ZIP and open Terminal in the `british-arena-cloud-dashboard` folder.
2. Run `cp .env.example .env` and edit the three required values: `ADMIN_PASSWORD`, `DISPLAY_KEY`, `SESSION_SECRET` (long, different secrets). For development only, leave `DEMO_MODE=true`.
3. Run `python3 server.py`.
4. Open **http://localhost:8765/login**, sign in, and use **http://localhost:8765/admin** to edit slides.
5. Open the TV view in another browser/window at **http://localhost:8765/display?token=YOUR_DISPLAY_KEY**. Never use the password as the display key.
6. Save your editor changes; the display checks for central configuration updates every 10 seconds.

For the Pi, use Chromium in kiosk mode pointed at the **HTTPS public display URL** when deployed. Keep the Pi's display token private.

## Railway deployment

- Create a Railway service from this source directory or a GitHub repository containing it. The Dockerfile runs Python with no third-party packages.
- Set Railway variables `ADMIN_PASSWORD`, `DISPLAY_KEY`, `SESSION_SECRET` (all long and independent), `DEMO_MODE=true`, `DATABASE_PATH=/data/arena.sqlite3` and optionally `NEWSLETTER_WEBHOOK_TOKEN`.
- Attach a **persistent Railway volume mounted at `/data`**. Without this, the SQLite content can disappear on redeploy or restart. Use a single app replica for this SQLite version.
- Expose a public HTTPS service using Railway's generated domain; optionally attach `social.britisharena.com` after verifying DNS and access.
- Visit `https://YOUR-SERVICE/login` and `https://YOUR-SERVICE/admin`; the Raspberry Pi opens `https://YOUR-SERVICE/display?token=YOUR_DISPLAY_KEY`.
- Use Railway's production deployment and database backups according to your requirements. Avoid exposing environment secrets, backup files and the display token publicly.

## Supported in this MVP

- Password-protected admin and session cookie; public internet requires HTTPS.
- Separate token-gated player view, without settings controls.
- Shared configuration saved as JSON in a SQLite database (on a persistent volume).
- Polling every 10 seconds on the Pi player for published changes.
- Existing Event Mode playlists, follower count demo, animations and signup QR codes.

## Important limitations

- The **first save** from the admin editor publishes the current slide configuration to cloud storage. If you have custom content in an older browser, export it from v11 first; v12 does not auto-import local v11 settings across browsers.
- Sponsor images currently embedded as Data URLs are constrained by the server's 3MB request cap. Optimise images before uploading; dedicated object storage should be the next step.
- This v12 MVP uses one admin password, one display token and one SQLite database. Device-specific credentials, multiple TV assignments, access logs, password resets, SSO, granular permissions and advanced deployment hardening are future work.
- **Live Instagram/Facebook counts require Meta API credentials**. Newsletter events require a webhook. Demo mode is simulated.
- The player URL contains a display token. Never share it openly. Rotate it if leaked.
- SQLite handles persistent configuration only; in-memory newsletter notifications are not durable across restarts.


## v13 Publishing and staff

- Admin login: username `admin` and the existing `ADMIN_PASSWORD`.
- Create marketing and viewer users under Settings > Publishing history / Team. Passwords must be 12+ characters.
- Save draft does not update the TV. Press **PUBLISH TO SCREENS** to update the published state.
- The player reads `/api/player/sync?token=<DISPLAY_KEY>` and updates every 5 seconds.
- Undo restores the previous published revision. History is stored in SQLite on the persistent `/data` volume.
- Marketing can save drafts and publish; viewers can preview. Admin can create accounts.
- Protect DISPLAY_KEY: it is a bearer token in the Pi player URL. Use HTTPS and don't share the URL publicly.
- The Pi offline caching service is NOT included in this cloud release; it is a separate player component.
