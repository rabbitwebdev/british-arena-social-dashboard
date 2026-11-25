# Live social milestone countdowns

Company targets can optionally display a live **remaining followers/likes** countdown on the TV, including a custom promotional message.

## Configure
1. Sign in to the staging company editor via `/organisations` → **Manage dashboard & TV screen**.
2. Under **Social milestone targets**, enable a target for Facebook followers, Instagram followers, or Facebook Page likes and enter a target number (example: 1,300).
3. Check **Show live countdown on TV**.
4. Enter a custom message containing the placeholder `{remaining}` once, for example:
   **Only {remaining} followers and you get happy hour!**
5. The editor displays a text preview using the available current social count. Click **Preview countdown** to see how it will appear on the TV layout, without publishing.
6. Click **Save draft**, then **Publish to TV**.

With 1,278 Facebook followers and a goal of 1,300, the message displays:
**Only 22 followers and you get happy hour!**

## Behavior and safety
- Each company's countdowns are independent, as are targets for followers and Facebook Page likes.
- The remaining amount is `target - current count`, never a fixed manually entered number.
- It automatically updates when the server's cached Meta follower count changes; the TV polls cached values every three seconds. Meta may return updated values later than the user's actual follow action.
- Countdown is displayed only when the relevant target is enabled, the countdown toggle is on, a valid live count exists, Meta connection status is healthy, and the target has not been reached.
- When a target is reached, the countdown disappears, and the existing one-time milestone celebration shows its configured animation and message.
- The countdown uses plain text rendering (no HTML injection). Backend validates the message length and requires exactly one `{remaining}` placeholder when enabled.
- **Preview countdown** is scoped to an authenticated company preview, uses a simulated count if no live value is available, and does not change Meta follower counts or milestone event state.
- Existing milestone settings without countdown fields remain compatible and default to countdown disabled.
- Both production Railway services remain untouched on `main`. This feature is only on `development` and Railway staging.

Tests cover countdown validation, per-channel settings, calculation examples, milestone crossing and one-shot behavior, and authenticated preview isolation.
