---
name: start-pod
description: Start a single cEDH playgroup game (one pod). Either let the data pick seats and decks for a goal, or seat it exactly as the user lists it, randoms included. Saves it, posts it to Discord with tags and reasons, and pushes it to the site's Current pod tab. Use for /start-pod, "start a pod", "one game with ...", or a pod template like "Pod: Darison – Dihada | Alex – Tayam | ...".
---

# /start-pod

A single game. It's a one-game session, so follow `/start-session`
(`.claude/skills/start-session/SKILL.md`) with `--games 1`, except when the
user gives the seating themselves.

**The user gives the seating** (players in seat order, maybe commanders, maybe
randoms):

```
/start-pod
Darison – Dihada | Alex – Tayam | Jordan (random) – Marneus | Eric – Thrasios/Tymna
Time limit: 80 min, win or draw
```

Use the manual scenario. It keeps exactly that seating, accepts randoms, and
gives no analysis:

```bash
export CEDH_PLAYGROUP_DIR="$PWD/data"
git pull --rebase --quiet
cd cedh-playgroup/scripts
python session.py start --date 2026-11-22 --games 1 --time-limit 80 --rules "win or draw" \
  -- --scenario generic --manual '[[{"player":"Darison","commander":"Dihada"},{"player":"Alex","commander":"Tayam"},{"player":"Jordan","guest":true,"commander":"Marneus Calgar"},{"player":"Eric","commander":"Thrasios/Tymna"}]]'
python announce.py
cd ../.. && git add data/pods.json && git commit -m "Start a pod for 11/22" && git push
```

- If a roster player's commander is missing, use their #1 practice deck.
- A random's commander is required. If it's missing, ask.

**The user gives players and a goal** (no seating): run `/start-session` with
`--games 1`.

Then tell them the seating and that players reply to the Discord post after
the game (or use the site's form). The result and everyone's takes get posted
automatically.
