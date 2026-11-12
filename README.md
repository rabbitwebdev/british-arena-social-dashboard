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

## v13.1 Railway login correction
Set `PUBLIC_ORIGIN=https://social-dashboard-production-ca90.up.railway.app` in Railway Variables.
This ensures secure POST requests are accepted when Railway forwards requests using an internal Host. Keep the origin exact, including https and excluding a trailing slash. Login failures returning HTTP 403 indicate an origin mismatch; incorrect passwords return 401.


## v13.2 login repair
Login requests now accept explicitly same-origin browser form submissions as identified by Fetch Metadata, avoiding false CSRF failures behind Railway's HTTPS reverse proxy. `PUBLIC_ORIGIN` remains the preferred fallback for older clients. Cross-site and same-site (subdomain-origin) POSTs are rejected.

## v13.3 settings fix
The cloud bootstrap runs the editor inside an asynchronous callback; inline controls require their handlers to be registered on `window`. This patch exports the existing handlers without changing database schema or login security. Replace only `index.html` in the GitHub repository.

## v14 Animation Studio
- Instagram / Facebook follower, newsletter and likes/reactions effect controls.
- New effects: confetti, floating reactions, Arena pulse wave, spotlight sweep, firework spark burst and bouncing count. Original roll, goal, pop and none remain.
- New presentation settings: counter scale, overlay opacity, accent colour, motion intensity, position and reduced-motion mode.
- Like/reaction animations are previews/manual-only until a supported trusted event integration is connected. No claim of real-time per-like Meta events.
- New setting `arenaDisplayStyle` is included in cloud draft/publish sync. Upload BOTH `index.html` and `server.py` to GitHub; keep `/data` Railway volume.

## v15: Connect Facebook & Instagram (Meta OAuth)

Under **Settings → Integrations** there is a **Connect Facebook & Instagram** button. This is a Meta-hosted consent flow; marketing users never generate access tokens manually. Only an admin can connect, change, or disconnect the Page. After login, choose the British Arena Facebook Page; the linked Instagram professional account is discovered automatically where accessible. The server stores the Page access token encrypted in the existing SQLite volume at `/data` and does not send it to the TV or marketing browsers.

### One-time administrator setup (Meta for Developers)

1. Create a Meta developer app owned by British Arena (https://developers.facebook.com/apps/) and configure **Facebook Login for Business**. The Page needs a linked Instagram **Business or Creator** account to connect Instagram via Facebook Login.
2. In Meta's Facebook Login OAuth settings, add **exactly** `https://social-dashboard-production-ca90.up.railway.app/meta/callback` as a Valid OAuth Redirect URI. Enable Web OAuth Login and client OAuth login as required by your app configuration.
3. In Railway → social-dashboard → Variables, set:
   - `META_APP_ID` (Meta app ID)
   - `META_APP_SECRET` (Meta app secret, private)
   - `META_TOKEN_ENCRYPTION_KEY` (Fernet key; generate on your Mac with `python3 -c "import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"` (standard-library command on your Mac))
   - `PUBLIC_ORIGIN=https://social-dashboard-production-ca90.up.railway.app`
   - `META_API_VERSION=v26.0` (verify the current supported version for your Meta app)
   - Keep the existing `ADMIN_PASSWORD`, `DISPLAY_KEY`, `SESSION_SECRET`, and `DATABASE_PATH=/data/arena.sqlite3`.
4. Deploy this updated version. Sign in as `admin`, open **Settings → Integrations**, press **Connect Facebook & Instagram**, approve the Page permissions on Meta, and select the correct Page.
5. The app requests `pages_show_list`, `pages_read_engagement`, `instagram_basic`. Additional permissions or app review may be required depending on your Meta app and which accounts will use it. Only request extra insights scopes when you actually add those features. The app must be granted Page access by a Meta user who has sufficient permissions.
6. The social counter poller switches to live follower counts after a successful connection, with updates at approximately 60-second intervals. This is **polling**, not an instant event for each new follower. Meta does **not** provide reliable universal per-like/per-follow notifications. Post likes and engagement metrics require separate integration work and permission checks.

### Security and maintenance

- Keep the Meta app secret and Fernet encryption key **only in Railway Variables**; never commit them to GitHub or put them in JavaScript.
- Back up the `/data` volume before an upgrade. Changing or losing the Fernet key will make previously stored tokens unreadable.
- Staff can use the existing Marketing role to manage slides and publish; Meta connection management is Admin-only.
- On disconnect, the saved Page token is removed from the local database. For full revocation, revoke the app in Meta's Business Integrations screen as well.
- When Meta permissions expire or are revoked, reconnect using the button. Dashboard content remains unaffected.
- This release implements the connector and OAuth callbacks, but **the live Meta authorisation cannot be verified without your app credentials and approval**.
