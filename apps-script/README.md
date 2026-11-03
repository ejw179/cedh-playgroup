# Results endpoint (Google Apps Script)

The website's **Report result** form sends each game here. Submissions are kept in a Google
Sheet you own. Each submission starts the repo's *Import pod results* workflow right away
(see "Instant updates" below), so it shows on the site, and in Discord, within a couple of minutes.

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

## Instant updates (GitHub token)

Without this, submissions wait for the workflow's schedule, which GitHub runs late or skips.

1. On GitHub: **Settings → Developer settings → Personal access tokens → Fine-grained tokens →
   Generate new token** (<https://github.com/settings/personal-access-tokens/new>).
   - Name: `cedh-playgroup import trigger`. Expiration: up to 1 year (set a reminder).
   - Repository access: **Only select repositories** → `ejw179/cedh-playgroup`.
   - Permissions → Repository permissions → **Actions: Read and write**. Nothing else.
   - **Generate token** and copy it.
2. In Apps Script: **Project Settings → Script Properties → Add script property**.
   Property: `GITHUB_TOKEN`, value: the token. **Save**.
3. **Deploy → Manage deployments → Edit (pencil) → Version: New version → Deploy**. Google asks
   you to authorize again, because the script now contacts GitHub. Allow it.

The token can only start this repo's workflows. It can't read or change code or secrets.

## Notes

- The PIN lives only in Script Properties. It is never in the repo or on the site.
- Anyone with the URL can *read* submissions (the site publishes them anyway). Only people
  with the PIN can *add* them.
- If you change `Code.gs` later: **Deploy → Manage deployments → Edit → Version: New version**.
  The URL stays the same.
- A bad submission can be deleted from the sheet's `results` tab before the next import. Once
  imported, ask Claude to remove the game from `data/games.json`.
