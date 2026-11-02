# cedh-playgroup

A Claude skill that runs a cEDH playgroup on top of [TopDeck.gg](https://topdeck.gg) tournament data:
players and decks, the recent meta, practice logs, data-driven practice pods with turn order,
and posting to the group's Discord.

- `cedh-playgroup/SKILL.md`: what Claude reads; the workflow and how to explain results
- `cedh-playgroup/scripts/`: stdlib-only Python
  - `sync.py`: pull EDH tournaments from TopDeck
  - `playgroup.py`: roster, decks, practice games, notes, who's playing each session
  - `analyze.py`: meta, matchups, player reports, seat/matchup profiles
  - `pods.py`: build pods, set turn order, explain each seat
  - `discord.py`: preview, then send on approval, via channel webhook
- `cedh-playgroup/evals/`: test cases (prompt, expected outcome, graders, synthetic data)

## Setup

```bash
setx TOPDECK_API_KEY "your-topdeck-key"           # free at https://topdeck.gg/account
setx DISCORD_WEBHOOK_URL "your-channel-webhook"   # optional; channel settings > Integrations > Webhooks
```

Group data lives in `~/cedh-playgroup/` (override with `CEDH_PLAYGROUP_DIR`), outside this repo.

## Build the installable skill

Run skill-creator's packager on the `cedh-playgroup/` folder to produce `cedh-playgroup.skill`.

Tournament data from TopDeck.gg (https://topdeck.gg). Credit is required by the TopDeck API terms.
