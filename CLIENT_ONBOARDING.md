# External client onboarding on Railway staging

## Operator workflow (invite-only)
1. Sign into https://arena-social-staging-staging.up.railway.app/login as the platform administrator.
2. Open /organisations and create the client's own company. Do not add the client to British Arena.
3. Inside that company's card, find **Invite a client or colleague**.
4. Enter the client's actual email address. Choose **Owner** for the client's primary contact, **Manager** for staff who can manage the Page and TV, or **Viewer** for read-only access.
5. Select **Send invitation**. If SMTP is configured, an invitation is emailed. If not, the interface explicitly states email was not sent and offers a one-time link to copy and send privately.
6. The client opens the link (expires in 48 hours), chooses a password of at least 12 characters, and lands on /onboarding/{their-company-id}.
7. The client selects **Connect my Facebook Page**, signs into their own Facebook account, chooses the Page, and then goes to their company's TV editor.
8. The client does not need to share their Facebook password or give the platform operator Page access.

## SMTP email delivery (optional until configured)
Add these Railway **staging-only** service variables using secure variable editing, never GitHub or chat:

- `INVITE_SMTP_HOST`: your authorised email provider SMTP server
- `INVITE_SMTP_PORT`: `587` (STARTTLS) or `465` (SSL)
- `INVITE_SMTP_USERNAME`: provider-issued SMTP username
- `INVITE_SMTP_PASSWORD`: provider-issued SMTP password
- `INVITE_FROM_EMAIL`: a verified sender address

Until the server is configured, clicking **Send invitation** creates a secure link, **not** an email. The link is returned only to the company owner who requested it and can be manually shared in a private email. SMTP failures also return the link to allow secure manual delivery.

## Meta tester requirements while Social Likes remains unpublished

1. Visit [Meta for Developers](https://developers.facebook.com/apps/) with the app-owner account and select the **Social Likes** app.
2. Find **App roles** / **Roles**, then **Testers** or **Add people** (labels vary by app-dashboard layout).
3. Add the client's Facebook account as a **Tester**, using the method Meta currently presents. Do **not** give them an Administrator or Developer role.
4. Ask your client to accept the invitation in their Facebook/Meta developer notifications or pending tester invitations. The Facebook account used for the test must have sufficient access to the client's own Facebook Page.
5. They must use that **same Facebook account** when they click **Connect my Facebook Page** inside Social Dashboard. The client company invitation email and Facebook login identity do not have to be the same email, but the Facebook account must be the approved app tester.
6. If the user is not eligible to join as a tester or permissions remain blocked, do not switch the app to Live merely to bypass testing. Follow Meta's role/review requirements.

Meta documentation: https://developers.facebook.com/docs/apps/managing-development-cycle and https://developers.facebook.com/docs/development/release/tech-providers/

Meta's app development mode restricts permission grants to app-role users. For general external customers, plan App Review/Advanced Access and Tech Provider verification where applicable. This rollout is a **limited, invited, test customer pilot**, not a commercial public launch.

## Security

- No public registration; only company owners can issue invitations.
- Invitations are 48-hour, one-use, organisation-scoped and email-bound, with hashed tokens stored in SQLite. Reissuing invalidates older unused links for that company/email.
- Passwords are stored with the existing PBKDF2 password hasher; the invitation URL carries the token in a URL fragment, rather than the HTTP request query.
- Rate limits: up to 10 invites per organisation per hour; seven failed logins trigger a 15-minute per-username lockout.
- Client logins redirect to company connections. A non-Arena client cannot access British Arena's /admin or legacy /api/counts /api/newsletter-events endpoints.
- Meta page tokens continue to be encrypted and organisation-scoped.
- We do not claim enterprise security certification or public launch readiness. Invite delivery, admin audit logging, forgotten-password recovery, broader multi-tenant review, and Meta Advanced Access must be tested/completed before commercial general release.
- The two production installations still track GitHub `main` and remain unchanged.

## Testing

Staging Railway runs `python -m unittest discover -s tests -v` prior to deployment. GitHub Actions on `development` does the same and compiles the key Python modules. Tests cover invitation expiry, replay, token hashing, account setup, company permissions, login throttling, and existing Meta/TV isolation.
