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

## Discord: tags and replies

Pods are posted to Discord with each player tagged, and players report the game by
**replying to their pod's post** ("I won turn 7 off Tayam, Jeff's removal was late").
Claude reads the replies, records the result with each player's take, posts the recap and
the next game. This needs a bot (a webhook can only post), the players' Discord IDs, and a
5-minute timer here. About 10 minutes, once.

**1. Make the bot** (<https://discord.com/developers/applications>)
- **New Application** → name it `cEDH Playgroup` → Create.
- **Bot** tab → **Reset Token** → copy it. It works like a password: don't paste it in chat.
- Same page, *Privileged Gateway Intents* → turn on **Message Content Intent** → **Save Changes**.
- **OAuth2** tab → *OAuth2 URL Generator* → scopes: **bot**; bot permissions: **View Channels**
  and **Read Message History**. Open the generated URL, pick the group's server, **Authorize**.
  Check that the bot can see the channel the pods are posted in.

**2. Save the token** (PowerShell, in the repo folder; it asks for the value, then paste):
```
gh secret set DISCORD_BOT_TOKEN -R ejw179/cedh-playgroup
```

**3. Players' Discord IDs.** In Discord: **User Settings → Advanced → Developer Mode** on. Then
right-click each player → **Copy User ID**. Build one line, `Name=ID` pairs separated by commas,
with names exactly as on the roster: `Eric=123456789012345678,Darison=234567890123456789,...`
```
gh secret set DISCORD_TAGS -R ejw179/cedh-playgroup
setx DISCORD_TAGS "Eric=123456789012345678,Darison=234567890123456789"
```
The first is for posts from the site and the import job, the second for `/start-session` run
from this PC. The IDs stay private: they're never written to the repo or the site.

**4. The timer.** Paste the latest [`Code.gs`](Code.gs) into the Apps Script editor, **Save**,
pick **installTrigger** in the function menu and click **Run** (authorize it). No redeploy needed.
Every 5 minutes it checks whether a session posted to Discord is being played (today or
yesterday, not finished). If so, it starts the import, which reads new replies.

How results are decided: once the replies say who won (or that it was a draw), the game is
recorded when every tagged player at the table has replied, or 15 minutes after the outcome
became clear. Replies that come in later are added as notes. Claude decides from the replies
(`ANTHROPIC_API_KEY`); without it, plain wording works: "I won", "Alex won", "draw".

## Notes

- The PIN lives only in Script Properties. It is never in the repo or on the site.
- Anyone with the URL can *read* submissions (the site publishes them anyway). Only people
  with the PIN can *add* them.
- If you change `Code.gs` later: **Deploy → Manage deployments → Edit → Version: New version**.
  The URL stays the same.
- A bad submission can be deleted from the sheet's `results` tab before the next import. Once
  imported, ask Claude to remove the game from `data/games.json`.
