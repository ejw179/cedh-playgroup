# cedh-playgroup

A Claude skill and website for running a cEDH playgroup on top of [TopDeck.gg](https://topdeck.gg) tournament data:
players and decks, the local / big-event / global meta, practice logs in turn order, data-driven practice pods,
and posting to the group's Discord.

**Site:** https://ejw179.github.io/cedh-playgroup (rebuilt nightly by GitHub Actions)

## Layout

- `cedh-playgroup/SKILL.md`: what Claude reads; the workflow and how to explain results
- `cedh-playgroup/scripts/`: stdlib-only Python
  - `sync.py`: pull EDH tournaments (with event coordinates) from TopDeck
  - `playgroup.py`: roster, decks, practice games, notes, sessions, home area
  - `analyze.py`: meta (`--local`, `--big`), matchups, player reports, seat/matchup profiles
  - `pods.py`: build pods, set turn order, explain each seat
  - `discord.py`: preview, then send on approval, via channel webhook
  - `build_site.py`: the static website
  - `import_results.py`: pull results reported on the site into `data/games.json`
- `data/`: the group's data (roster, practice decks, games and per-player notes, sessions, saved pods,
  settings). Public: it feeds the site.
- `apps-script/`: the Google Apps Script that receives "Report result" submissions (setup in its README)
- `cedh-playgroup/evals/`: test cases (prompt, expected outcome, graders, synthetic data)
- `.github/workflows/site.yml`: nightly sync, build, deploy to GitHub Pages
- `.github/workflows/import.yml`: started by each site submission (10-minute schedule as fallback): import results, plan sessions, post them to Discord, rebuild

## Setup

```bash
setx TOPDECK_API_KEY "your-topdeck-key"                       # free at https://topdeck.gg/account
setx DISCORD_WEBHOOK_URL "your-channel-webhook"               # optional
setx CEDH_PLAYGROUP_DIR "C:\Admin\git\cedh-playgroup\data"    # where the skill reads and writes group data
gh secret set TOPDECK_API_KEY                                 # for the nightly site build
```

## Build

- Site, locally: `python cedh-playgroup/scripts/build_site.py --out site`
- Installable skill: run skill-creator's packager on `cedh-playgroup/` to produce `cedh-playgroup.skill`

Tournament data from TopDeck.gg (https://topdeck.gg). Credit is required by the TopDeck API terms.
