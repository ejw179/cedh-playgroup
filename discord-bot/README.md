# Discord slash commands

`/start`, `/report` and `/pods` in the group's server. A small Cloudflare Worker (`worker.js`, free plan) receives
them and sends the same submissions the site's forms send to the Apps Script, so planning, recording and posting run
through the usual import workflow. Nothing runs on your PC.

| Command | Does |
|---|---|
| `/start players: Eric, Darison, Alex, Jeff goal: Worst-seat practice games: 3` | Plans the session. Also: `best_decks`, `objective`, `time_limit`, `rules`, `date`, `player` / `target` (Improve, Fix), `replace` |
| `/report winner: Alex note: …` | Records the current game of the winner's pod (pick from the list, or Draw). Your note is saved as your take; the others can still reply to the pod's post |
| `/pods` | Shows the seating of the game being played now |

Replies to the Worker are only visible to whoever ran the command. The pods and results post to the channel as
before, usually a minute or two later.

## Setup (once)

1. **Discord app.** Use the app you made for "Discord replies" (apps-script/README.md), or create one at
   <https://discord.com/developers/applications>. From **General Information** note the *Application ID* and
   *Public Key*. Invite it again with the commands scope (the same invite works if it's already in the server):
   `https://discord.com/oauth2/authorize?client_id=APP_ID&scope=bot+applications.commands`
2. **Worker.** Put the Public Key in `wrangler.toml` as `DISCORD_PUBLIC_KEY`, then:

   ```bash
   cd discord-bot
   npx wrangler login
   npx wrangler secret put PIN            # the group PIN (Apps Script property PIN)
   npx wrangler secret put DISCORD_TAGS   # same value as the GitHub secret: Eric=123...,Alex=456...
   npx wrangler deploy
   ```

   It prints the Worker URL, e.g. `https://cedh-playgroup-discord.<you>.workers.dev`.
3. **Endpoint.** In the Developer Portal, **General Information > Interactions Endpoint URL**: paste the Worker URL
   and save. Discord checks it straight away; if it refuses, the Public Key in `wrangler.toml` doesn't match.
4. **Commands.** In the server, right-click it > *Copy Server ID* (turn on Developer Mode in Discord's Advanced
   settings first). Then:

   ```bash
   setx DISCORD_APP_ID "..."
   setx DISCORD_GUILD_ID "..."
   python discord-bot/register.py   # uses DISCORD_BOT_TOKEN too
   ```

   Run `register.py` again after changing its command list. Changes to `worker.js` only need `npx wrangler deploy`.

Optional `wrangler.toml` vars: `GUILD_ID` (only that server) and `ALLOWED_ROLE` (only members with that role ID).
Optional secret `GITHUB_TOKEN` (read-only, Contents) makes the Worker read data through the GitHub API instead of
raw files.

## Notes

- `/start` refuses a date that already has an unfinished session unless you pass `replace: True`. Players can be names
  or @mentions (mentions need `DISCORD_TAGS`). Randoms still need a hand-built pod on the site.
- `/report` won't report a pod that's already in `games.json`, and the import skips a second Discord report of the
  same pod (two people reporting at once).
- The PIN stays in the Worker; anyone in the server (or with `ALLOWED_ROLE`) can use the commands.
