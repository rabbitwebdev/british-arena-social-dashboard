# Social follower and Page-like milestone targets

On Railway **staging** (GitHub branch `development`), each company can set independent one-time milestones for Facebook followers, Instagram followers and Facebook Page likes.

## Configure a milestone
1. Open `/organisations`, choose **Manage dashboard & TV screen** for the company.
2. Scroll to **Social milestone targets**.
3. Enable a channel and enter the numeric goal, for example `1233` Facebook followers.
4. Enter a custom message, for example **Target reached, drinks are on us**.
5. Select a celebration: Confetti, Fireworks, Football or Celebration.
6. **Save draft** then **Publish to TV**.
7. Leave the TV display running. The server checks fresh Meta counts on its normal polling interval (staging can be as low as 5 seconds) and the TV reads the cached result every 3 seconds.
8. When a verified count reaches the configured number, the TV shows the full-screen animation and message for 8 seconds.

## Rules
- One celebration per enabled target **per company, per metric**. A count briefly dropping and rising to the same goal cannot replay it.
- Publishing a goal that is **already reached** does not retroactively celebrate. Choose a target above the current count to test.
- When there is no reliable current count, the first successful poll establishes a baseline without triggering.
- Changing the number to a new goal rearms the milestone, while changing only the wording or animation of an already reached goal does not replay it.
- Disable or remove a milestone to clear it. Each company's milestones are independent and stored in SQLite on its persistent Railway volume.
- The status updates when Meta provides fresh values. Meta's follower figures are polled; they are not real-time follow event webhooks.
- The new celebratory overlay uses textContent, never inserts custom message text as HTML.
- The original smaller per-follower celebration remains; a newly achieved target triggers the larger milestone overlay.
- Milestone state is deleted when a company is deleted.
- Existing company settings and both production Railway installations are unchanged.

## Testing
Automated staging tests verify threshold crossing, non-replay on reloads/count dips, independently tracked social metrics, valid custom text/animation, target rearming, privacy between companies, and handling already-met/unknown baselines.
