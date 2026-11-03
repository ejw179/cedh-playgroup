---
name: start-session
description: Start a cEDH playgroup session of one or more games. Builds the pods, decks and seat order from the group's tournament and practice data, saves them, posts them to Discord with player tags and the reasons behind each seat, and pushes so the site's Current pod tab shows them. Later games are re-planned automatically as results come in. Use for /start-session, "start a session", "set up tonight's games", or a filled-in session template.
---

# /start-session

The user gives something like:

```
/start-session
Date: Sat 11/22            (optional, default today)
Games: 3
Players: Eric, Darison, Alex, Jeff
Goal: Tournament practice, best decks, worst seat. Objective: stress-test politics.
Time limit: 80 min, win or draw
```

Any wording is fine. If players or the goal are missing, ask once, in a single
short question. Never ask for anything else.

## 1. Read the request

- **Players:** match each name to the roster (`data/roster.json`); obvious
  typos are fine ("Darion" is Darison). Anyone not on the roster is a random. A
  data-driven scenario can't seat a random, so offer `/start-pod`-style manual
  seating for that pod instead.
- **Games:** default 1. Each game after the first is planned automatically once
  the one before is reported. You don't plan them now.
- **Goal → scenario.** Pick the closest match. A combined goal takes the seat
  rule from its seat words, and "best decks" means everyone plays their #1
  practice deck.

| They say | pods.py args |
|---|---|
| worst seat, weak seats | `--scenario worst_seat` (#1 decks already) |
| best seat, tournament rehearsal, tournament practice (no seat words) | `--scenario rehearsal` |
| local meta | `--scenario local` |
| big events / big meta | `--scenario big` |
| help / improve PLAYER | `--scenario improve --focus PLAYER` |
| PLAYER needs reps vs CMDR | `--scenario fix --fix PLAYER=CMDR` |
| exact decks or seats named | `--scenario specific --deck P=CMDR --seat P=N` |
| random | `--scenario random` |
| "best decks" with another scenario | add `--first-deck` |

  Put the user's own goal and objective words in `--goal` ("Tournament
  practice: best decks, worst seat. Stress-test politics."). They show on the
  site and in Discord as written.
- **Time limit / rules:** `--time-limit 80 --rules "win or draw"`.

## 2. Plan, post, push

Run from the repo root. The data lives in `data/`:

```bash
export CEDH_PLAYGROUP_DIR="$PWD/data"
git pull --rebase --quiet
cd cedh-playgroup/scripts
python session.py start --date 2026-11-22 --games 3 \
  --goal "Tournament practice: best decks, worst seat. Stress-test politics." \
  --time-limit 80 --rules "win or draw" \
  -- --scenario worst_seat --players Eric Darison Alex Jeff
python announce.py
cd ../..
git add data/pods.json && git commit -m "Start the 11/22 session (3 games, worst seat)" && git push
```

- If `start` says a session that day is in progress, ask whether to start
  over. On yes, rerun it with `--replace`.
- `announce.py` posts the header and one message per pod. Each pod message
  tags its players and gives the reason for each seat and deck. The group
  approved these posts going out without a preview. It saves the Discord
  message ids in `pods.json`, which is why the commit comes after it. If it
  says the webhook isn't set, say so and carry on.
- If `TOPDECK_API_KEY` is missing or the cache is empty, run `python sync.py
  --days 90` first.

## 3. Tell the user

Give the seating (seat 1 goes first) with a one-line reason per player. Take
the reasons from `python session.py status` or the `-#` lines `announce.py`
printed. Then explain how the session continues:

- Players **reply to their pod's Discord post** after each game: who won (or
  draw) and their take. The site's Current pod tab also has a report form.
- Each result is posted to Discord with every player's perspective. The next
  game is then re-planned (same decks, new seats) and posted, all automatically.
- The site updates about a minute after each step.

Later in the session, "game 2 Darison won, notes: …" in chat goes into the
same flow. Write it as a result payload and import it, or log it with
`playgroup.py log-game` adding `--date`, `--pod` and the game number. Then run
`session.py advance`, `announce.py` and push.
