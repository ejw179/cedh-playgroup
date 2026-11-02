# Results endpoint (Google Apps Script)

The website's **Report result** form sends each game here. Submissions are kept in a Google
Sheet you own, and the repo's *Import pod results* workflow pulls them every 30 minutes.

## Set up (about 5 minutes, once)

1. Go to <https://sheets.new> and name the sheet **cEDH Playgroup results**.
2. In the sheet: **Extensions → Apps Script**.
3. Delete what's in `Code.gs`, paste in the contents of [`Code.gs`](Code.gs), and click **Save**.
4. Set the group PIN: **Project Settings** (gear icon) → **Script Properties** → **Add script property**.
   Property: `PIN`, value: the code your group will type on the form (for example 4–6 digits). **Save**.
5. **Deploy → New deployment** → gear icon → **Web app**.
   - Description: `results`
   - Execute as: **Me**
   - Who has access: **Anyone**
   - Click **Deploy**, then **Authorize access** and allow it. If Google warns that the app isn't
     verified, choose **Advanced → Go to (project) (unsafe)**. It's your own script.
6. Copy the **Web app URL**. It ends in `/exec`. Give it to Claude, which saves it as `results_url`
   in `data/config.json` and pushes so the site's form starts working.

## Notes

- The PIN lives only in Script Properties. It is never in the repo or on the site.
- Anyone with the URL can *read* submissions (the site publishes them anyway). Only people
  with the PIN can *add* them.
- If you change `Code.gs` later: **Deploy → Manage deployments → Edit → Version: New version**.
  The URL stays the same.
- A bad submission can be deleted from the sheet's `results` tab before the next import. Once
  imported, ask Claude to remove the game from `data/games.json`.
