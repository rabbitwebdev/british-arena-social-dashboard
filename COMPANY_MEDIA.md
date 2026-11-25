# Company image uploads (development/staging)

Company Owners and Managers can upload a logo and sponsor/event slide images directly in their own company editor.

## How to use
1. Open the staging company dashboard at `/organisations`, then select **Manage dashboard & TV screen**.
2. In **TV design**, choose a PNG/JPG/WebP using **Upload logo**. The image is uploaded immediately, and its private media reference is placed in the logo field. You can still paste an HTTPS image URL instead.
3. Under **Slides**, add a slide and use **Or upload an image**, or paste an HTTPS image URL.
4. Check the image previews. Click **Save draft** then **Publish to TV**. Only published images appear on TV. Publish does not automatically replace other companies' branding.
5. Use **Clear logo** to remove the active logo reference from the configuration. The original uploaded file remains in the private company storage for now.

## Security and persistence
- Images use the existing dedicated staging Railway `/data` volume alongside the SQLite database; directory is `/data/company-media/<company-id>/`. No files are committed to Git.
- Maximum 8 MB upload and 20 million source pixels; 100 files / 150 MB storage per company. Upload MIME and file contents are checked server-side. Only PNG/JPG/WebP are supported.
- The server decodes and re-encodes every image as WebP at max 2560px on either side, stripping metadata and preventing executable SVG uploads.
- Media references have the format `media:<32 hex characters>`. The server rejects cross-company references in configuration.
- Unpublished images require authenticated organisation membership to view. TV images are available via the company's private, revocable screen link only when referenced by **published** settings.
- Image deletion, media-library browsing and bulk clean-up are not included in this iteration. Unused files remain until an explicit retention/deletion workflow is added.
- Existing HTTPS image URLs remain supported. For production launch, consider proxying remote images or applying a restrictive host allow-list.
- This change is for `development` and Railway staging only. Both production environments remain on `main`.

## Tests
The pre-deploy suite includes safe image conversion, rejection of invalid formats/oversized uploads, authenticated uploads, cross-tenant access, published-versus-draft image delivery and legacy screen regressions.

**Note:** Staging image storage is limited by the existing 1 GB persistent Railway volume. Review storage capacity and back-ups before onboarding many paying customers.
