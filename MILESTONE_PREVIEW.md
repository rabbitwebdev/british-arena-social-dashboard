# Preview a social milestone celebration

On Railway staging, open a company's **Manage dashboard & TV screen** page and find **Social milestone targets**.

Each Facebook followers, Instagram followers, and Facebook Page likes target now has a **Preview celebration** button.

- Edit the target number, custom message, and animation, then select **Preview celebration**. You do not need to publish or reach the goal to preview.
- A 16:9 TV preview opens in a modal and displays the same full-screen overlay used by the real screen.
- Select **Replay celebration** to test it again, then **Close** to return to the editor.
- The preview reads current editor values; it does not update the follower counts, the published goal, or the milestone state in SQLite.
- Only signed-in members of the company can open the preview URL at `/company/{id}/preview`. A viewer can preview, but cannot edit or publish targets.
- The base TV branding/background shown comes from the company's saved draft settings; unsaved changes to the milestone message, target number, and animation are used immediately.
- The live TV still uses the one-time threshold crossing. Preview does not send anything to real display screens.

The feature is available on the `development` branch and isolated staging environment only.
