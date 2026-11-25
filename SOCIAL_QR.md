# Social-page QR codes (staging)

Company owners and managers can now add on-screen QR codes for their Facebook and Instagram pages.

## Configure
1. Sign into https://arena-social-staging-staging.up.railway.app/organisations and open **Manage dashboard & TV screen**.
2. Under **TV design**, enter the official HTTPS Facebook or Instagram Page URLs. A connected Meta Page does not automatically populate these editable website URLs yet.
3. In **Social page QR codes**, check **Display social QR codes** and select Facebook, Instagram, or both.
4. Pick **Below follower counts** or **Beside follower counts**, and a size (Small 120, Medium 180, Large 240, Extra large 280).
5. Click **Save draft**, verify the QR preview if available, then **Publish to TV**.
6. Refresh the company TV display if necessary. The display automatically checks for changes and the published QR codes lead to the configured social pages.

## Security and technical details
- QR SVGs are generated in the application using Segno. The dashboard makes no request to a third-party QR generation service.
- Official social network HTTPS domains are required when QR mode is used. The URLs are validated server-side. QR generation cannot encode arbitrary phishing destinations.
- Draft QR images are available only to authenticated company members; public TV QR images are available only through the company's private, revocable screen link.
- QR settings are opt-in and kept separate per organisation.
- Existing company configurations remain compatible; no QR code is displayed until explicitly enabled and published.
- This upgrade is in **development / Railway staging** only, not the two production installations.

## Tests
The staging service predeploy command runs `python -m unittest discover -s tests -v` including QR route permissions, publishing boundaries, validation, and SVG generation.
