---
name: cedh-playgroup
description: Runs a cEDH (competitive Commander) playgroup on top of TopDeck.gg tournament data. Use it to add players and their decks, pull each player's tournament results from TopDeck.gg, report the recent meta (last month by default), log practice games, notes and who can make a session, and build practice pods that face the top meta, stay balanced, avoid repeat pairings and target each player's weak matchups, and post pods, meta reports, results and "who's in?" calls to the group's Discord channel. Use it whenever the user mentions their playgroup, pod or practice night, testing partners, TopDeck stats for people they play with, "what's the meta right now", a practice game result to record, seating people for cEDH, or posting any of that to Discord, even if they don't say "skill" or "TopDeck".
---

# cEDH Playgroup

This skill keeps a small, hand-editable record of one cEDH playgroup and
combines it with tournament data from TopDeck.gg. It has four jobs, plus
posting their results to the group's Discord:

1. **Players**: add people by their TopDeck profile ID, along with the decks they own.
2. **Meta**: what's being played and what's winning, last 30 days by default.
3. **Input**: practice results, current decks, meta notes, availability.
4. **Pods**: seat a practice night so the group gets useful reps.

All the arithmetic lives in Python scripts in `scripts/` (stdlib only). Your
job is to run them, read their output, and turn it into advice the group can
use. Don't recompute stats by hand. The scripts handle the edge cases
(partner names, draws, small samples), and doing it twice risks two
different answers.

## Setup and where things live

- Scripts: `scripts/` beside this file. Run them as `python <skill-dir>/scripts/<name>.py ...`.
- Data: `~/cedh-playgroup/` (override with the `CEDH_PLAYGROUP_DIR` env var).
  `roster.json`, `games.json`, `notes.json`, `availability.json`, and
  `cache/tournaments.json` (the TopDeck pull). They're plain JSON, so it's fine
  to show them to the user or let them edit by hand.
- TopDeck API key: `TOPDECK_API_KEY` env var. It's free at https://topdeck.gg/account.
  If it's missing, say so and explain how to set it. Don't ask the user to paste
  the key into chat.

**Attribution is a condition of the TopDeck API.** Every answer that uses
tournament data ends with: *Tournament data from TopDeck.gg (https://topdeck.gg)*.
The scripts print it. Keep it when you summarise.

## Freshness: sync first

TopDeck has no "results for player X" endpoint. `sync.py` pulls every
completed EDH tournament in a date window, and all the player and meta stats
come from that cache. Before a meta report, player stats or pods, check how
old the cache is (the `synced` list in `cache/tournaments.json`). If it's older
than about a day, or doesn't cover the window you need, sync:

```bash
python scripts/sync.py              # last 30 days, fetched a week at a time
python scripts/sync.py --days 60    # also needed for the meta "Δ share" trend column
python scripts/sync.py --import saved_response.json   # offline / testing
```

The bulk endpoint is rate-limited. The script backs off on HTTP 429 by itself,
so let it run. A 30-day sync can take a minute or two.

## 1. Players

```bash
python scripts/playgroup.py add-player "Eric" --topdeck https://topdeck.gg/profile/<id> --deck kinnan
python scripts/playgroup.py set-decks "Sam" --deck najeela --deck "tymna kraum" --primary najeela
python scripts/playgroup.py set-decks "Lee" --deck "magda" --add      # add without replacing
python scripts/playgroup.py set-decks "Jo" --deck glarb --gauntlet-pilot yes
python scripts/playgroup.py remove-player "Max"
python scripts/analyze.py players            # everyone; or: players Eric Sam
```

- `--topdeck` takes a profile link, either form (`topdeck.gg/profile/<id>` or
  `topdeck.gg/profile/%40handle`), an `@handle`, or a bare ID. Tournament
  standings only know the raw player ID, so the script opens the profile page
  and reads the ID from its `player-id` meta tag. It prints the TopDeck display
  name it found. If that name doesn't look like the person the user meant, say
  so. If the lookup fails (offline), it stores what was given and warns. Re-add
  the player later so their stats match. If the user gives only a name, ask
  for their TopDeck profile link. Matching by display name picks up strangers
  with the same name.
- Commander names are fuzzy-matched to TopDeck's spelling ("tymna kraum" →
  "Tymna the Weaver / Kraum, Ludevic's Opus"). The script prints the match it
  chose. If it lists alternatives, confirm with the user.
  `playgroup.py resolve <name>` checks a name without saving anything.
- A **gauntlet pilot** is willing to play one of the group's shared decks
  instead of their own. That's how top-meta decks nobody owns end up in pods.
- A player with zero TopDeck events in the window isn't an error. They just
  haven't played a tracked event lately. Offer `sync.py --days 90` for more history.

When reporting players, lead with what's useful for practice: current deck,
recent form (record and finishes), and what their practice log says. Don't
just reprint the table.

## 2. Meta report

```bash
python scripts/analyze.py meta                       # 30 days, 16+ player events, top 25
python scripts/analyze.py meta --days 14 --min-players 32 --top 15
python scripts/analyze.py matchups "kinnan"          # who this deck wins and loses against
python scripts/analyze.py matchups --player Eric     # the same, from one player's own tables
```

Columns: entries, meta share, Δ share against the previous equal window, win
rate, draw rate, top-cut conversion, and best finish.

**Three lenses on the meta.** Pick the one the question is about, or show
local and big side by side when the user just asks "what's the meta":

| Lens | Command | Answers |
|---|---|---|
| Local | `meta --local` | What our group will actually face: events within the saved radius of home, every size, last 30 days |
| Big events | `meta --big` | What wins at big tournaments: over 100 players, anywhere, last 90 days (they're rare) |
| Global | `meta` | Everything on TopDeck with 16+ players |

Set the home area and the "big" threshold once with
`playgroup.py home --zip 18201 --miles 100 --big-min 101`, or use a one-off
`--zip Z --miles M`. The local view uses TopDeck's event coordinates. Online
events and events without a location can't be placed, and the report says
how many are missing. Local samples are small, so call shares "who shows
up" and win rates hints. Where local and big disagree, that's the useful
part: a deck that's popular locally but weak at big events, or one that
wins big events but nobody near you plays it yet. `pods.py --local` or
`--big` makes "face the top meta" use that lens.

Reading it well matters more than printing it:
- **Win rates are four-player numbers.** About 25% is average, and lower once
  draws are counted. The report prints the window's observed average. A 30%
  deck is very strong. Never read win rates against 50%.
- **Small samples lie.** Under about 20 entries, or about 10 shared tables in
  `matchups`, call a number a hint, not a finding.
- Popularity (share) and strength (win rate, conversion) are different
  questions. Point out decks that over-perform their share, and popular decks
  that convert poorly.
- `--min-players 16` keeps small locals out of the meta view. If the user asks
  about their local scene, lower it.
- Bring in the group's own `notes.json` where it's relevant ("you noted lots
  of Magda at the LGS, and it's also up 2% globally").

Shape the answer as: 3–5 bullet takeaways, then the table (trim to what
matters), then what it means for this group's practice (for example, "nobody
here owns a Najeela, and it's #3; worth a gauntlet copy").

## 3. Input: results, decks, notes, availability

```bash
python scripts/playgroup.py log-game --date 2026-11-08 \
    --seat "Eric=Kinnan" --seat "Sam=Najeela" --seat "Lee=Tymna Kraum" --seat "Kim=Yuriko" \
    --winner Sam                     # or --winner draw; add --no-order if turn order is unknown
python scripts/playgroup.py note "Lots of Magda at the LGS lately" --author Eric --tag meta
python scripts/playgroup.py avail 2026-11-08 Eric Sam Lee Kim Jo      # --replace to overwrite
python scripts/playgroup.py gauntlet-deck add "Najeela" --list <url>
python scripts/playgroup.py show games      # roster | games | notes | avail
```

People report results casually ("sam won on najeela, me lee and kim on
kinnan tymna yuriko, then a draw"). Turn each game into one `log-game` call.

**Seat order is turn order**, and it matters: across TopDeck's 4-player
tables, seat 1 wins about 23% and seat 4 about 14%. List `--seat`s in turn order
(seat 1 first). If the report doesn't make turn order clear, ask once ("who
went first?"). If they don't know, log with `--no-order`. The game still
counts for records and matchups, just not for seat stats. Tell the group the
best report format, because it feeds everyone's seat profile:
`G1 (turn order): Eric Kinnan, Sam Najeela, Lee Tymna/Kraum, Kim Yuriko. Winner Sam`.
If anything that changes the stats is ambiguous, such as who won or which
deck someone was on, ask before logging. Afterwards, read back what was
recorded in one line per game so mistakes get caught.

If the user pastes several games, log them all, then summarise the night:
who won what, and any running streaks.

## 4. Practice pods

### Who's playing this session

Attendance changes every week, so settle who's in before building pods. In
order of preference:
1. The user names people ("everyone but Jeff", "Eric, Ryan, Saige, Alex").
2. A list already saved for that date (`playgroup.py show avail`).
3. Ask. If your client has a multiple-choice question tool, list the roster as
   checkboxes, splitting it across questions if there are more names than one
   question allows, and ask for the date too. Otherwise ask in one line.

Save the answer so pods, the Discord post and later reports agree:

```bash
python scripts/playgroup.py avail 2026-11-08 --all --remove Jeff Saige   # everyone except
python scripts/playgroup.py avail 2026-11-08 Ishmael                      # add a late yes
python scripts/playgroup.py avail 2026-11-08 --remove Ryan                # drop a late no
python scripts/pods.py --date 2026-11-08                                  # build from the saved list
python scripts/pods.py --all --except Jeff                                # one-off, without saving
```

If the list changes after pods were posted, rebuild and say who moved. Don't
quietly re-seat people who already saw their pod.

```bash
python scripts/pods.py --date 2026-11-08               # uses that night's availability
python scripts/pods.py --players Eric Sam Lee Kim Jo Ana Max
python scripts/pods.py --date 2026-11-08 --focus Eric  # Eric's weak matchups count 4x
python scripts/pods.py --date 2026-11-08 --deck "Sam=Tymna Kraum" --w-repeat 0
```

The builder searches seatings and deck choices and scores them on four goals,
all on by default. Turn a goal off with its weight set to 0, or raise it to
emphasise it:

| Goal | Flag | What it rewards |
|---|---|---|
| Face the top meta | `--w-meta` | top-N meta decks at each table (`--top-meta 15`) |
| Balanced pods | `--w-balance` | similar average strength (player record + deck win rate) per pod |
| Avoid repeats | `--w-repeat` | not re-seating pairs who sat together in recent logged games |
| Target weak matchups | `--w-matchup` | seating a player across from decks *they* lose to (see below) |

**Whose bad matchups?** "Work on my bad matchups" is about the person, not
the deck. The builder estimates each player's win rate against each opposing
commander from their own TopDeck tables and practice games. Those are small
samples, so it shrinks them toward how that deck does field-wide in the same
pairing. A pilot who's 1/11 into Rograkh/Thrasios still counts as weak there,
even if Kinnan players do fine against it overall. Before building pods for
someone who asked about their matchups, look at the evidence yourself:

```bash
python scripts/analyze.py matchups --player Eric --days 90   # Eric's own tables, every event size
python scripts/analyze.py matchups kinnan                    # the deck field-wide, for contrast
```

When the two disagree, say so plainly ("Kinnan is fine into Najeela in general,
but you're 0/3 against Sam's"). It's useful coaching: the gap is in the
player's lines, not the deck.

**Seats.** The builder also sets each pod's turn order. With `--seat-mode
practice` (the default), each player goes in the seat where their record is
weakest *relative to what that seat normally wins*. Going 0/6 in seat 2 is bad.
Going 2/10 in seat 4 is about par. `--seat-mode best` rehearses their best
seats, for example before a tournament, and `random` just shuffles. Per-player
seat and matchup evidence:

```bash
python scripts/analyze.py profile            # everyone; or: profile Eric Ryan
```

**Explaining the pods: this is the point.** For every seat the script prints
the player's evidence count, seat read (worst, best, or no read yet, with
record and points against the seat average), the matchups at this table
("needs practice vs …" and "favoured vs …", with their own record and the
field rate), and weak matchups not at this table. Turn that into a short,
data-driven "why" per player, for example:

> **Seat 1: Alex on Tayam.** Seat 1 is Alex's worst seat (1/10, about 7 pts below
> what seat 1 usually wins), so he practises it here. He's also 0/7 against
> Sisay at tournaments, and Darison's Sisay is at this table. He's favoured
> into Ob Nixilis, so that's one he should be winning.

Rules for those explanations:
- Quote the actual records (1/10, 0/7). They're what makes it credible.
- Say "thin sample" when it rests on under ~6 games. Don't oversell a 0/2.
- If a player has no data (no TopDeck link, no logged games), say their
  seat is a placeholder and that practice reports will fill it in. Don't
  invent a reason.
- End with what more practice data would sharpen. Every logged game in turn
  order improves the seat and matchup reads.

Pod sizes are fours, with threes absorbing the remainder. With 5 players, one
sits out and rotates in. If the user would rather play a 5-pod, say that's
their call and seat everyone. The output's "Sitting out" line names who to
rotate.

Present each pod with **why it's built that way**, in plain language: which
meta decks they'll see, which weak matchup each player is there to practise,
and any repeat pairing that couldn't be avoided. The script's notes are raw
material. Pick the one or two that matter per pod, and don't paste all of
them. If a gauntlet deck was assigned, make sure the pilot knows. Suggest
re-running with a different `--seed` if they want an alternative seating.

If the meta cache is empty, the meta and matchup goals have nothing to work
with. Say so and sync first, or build pods on balance and repeats alone.

## 5. Posting to the group's Discord

The group's channel takes posts through a webhook. Its URL lives in
`DISCORD_WEBHOOK_URL` (env var or saved Windows user variable). The webhook
URL works like a password: anyone holding it can post as the group. If it's
missing, tell the user how to make one (channel settings → Integrations →
Webhooks → New Webhook → Copy Webhook URL) and to save it themselves with
`setx DISCORD_WEBHOOK_URL "<url>"`. Don't ask for it in chat, and never echo it back.

**A post is public and can't be unsent by you, so every send is approved by
the user first.** The script enforces this by splitting preview and send:

```bash
python scripts/discord.py preview --kind pods --file draft.md   # kinds: pods, meta, results, availability, other
# show the user the printed messages, wait for a clear yes, then:
python scripts/discord.py send "<outbox file printed by preview>"
```

`send` posts the saved preview byte for byte. It refuses a file that changed
after preview, or one that was already sent. If the user asks for edits, write
a new draft and preview again. Approval covers that one post only. "Post the
pods" earlier doesn't cover a later meta report.

**Write for Discord, not for this chat.** People read it on their phones, between games:
- No markdown tables. Discord prints the pipes. Use short bullet lists.
- `**bold**`, `*italics*`, `> quote`, `-# small text` and `## headings` all render.
- Keep it short. The full reasoning stays in chat. The post carries the result
  and one line of "why" at most.
- @mentions don't ping unless you pass `--allow-mentions`. Only pass it if
  the user asked to ping people.
- `pods`, `meta` and `results` posts get the TopDeck credit line added
  automatically if your draft doesn't have one. Keep it: it's a condition of
  the API.

What each kind should hold:

| Kind | Content |
|---|---|
| `pods` | Date, then each pod as a bullet list: player, deck, and "(gauntlet deck)" where it applies. One line on what each pod is practising. |
| `meta` | 3–5 bullets: what's on top, what's rising, what over-performs, and what the group is missing. Window and event-size filter in small text. |
| `results` | The night's games (pod, winner) and one line on standout records or streaks. |
| `availability` | "Who's in for <day date>?", plus how to answer (react or reply). The webhook can't read replies, so afterwards the user tells you who's in and you record it with `playgroup.py avail`. |

Typical flow: build or log something → show the full answer in chat →
offer "Want me to post this to Discord?" → on yes, write the draft, preview,
show it, and send on a second yes. If the user already said "and post it to
Discord" in their request, one look at the preview is still required before
sending.

## Practice decks, takeaways, and closing a pod

**Practice decks.** Each player can list 1–3 decks they want reps on, in
priority order, with decklist links. Pods seat them on #1 unless it clashes:

```bash
python scripts/playgroup.py practice Eric --deck "Kinnan=https://moxfield.com/decks/..." --deck "Tymna Kraum"
```

**Takeaways are per pod, per player.** When a game is logged, each player's
note (what they learned, what to work on) is stored on that game. The newest
one is that player's current takeaway. It shows on their profile, in their
seat on the next pods, and in `pods.py` output.

```bash
python scripts/playgroup.py log-game --date 2026-11-08 --pod 1 \
    --seat "Alex=Tayam" --seat "Darison=Sisay" --seat "Eric=Kinnan" --seat "Ryan=Tymna Kraum" \
    --winner Darison --note-for "Eric=Held Pact too long vs Sisay" --list-for "Darison=https://..."
python scripts/playgroup.py takeaways            # everyone's latest
```

**Use takeaways to make pods actionable.** Before building pods, read
`takeaways`. When a note names a deck or an archetype ("can't beat Sisay",
"struggle vs stax", "need reps on turn-2 Kinnan"), steer the pods toward it:
`--focus` that player, put the named deck at their table with `--deck` or a
gauntlet pilot, or seat them on the practice deck the note is about. Then say
so in the why ("Eric's at Darison's table because his last takeaway was about
Sisay"). If a note is vague, don't invent a target.

**Lock the seating, then close the pod on the site.** Once the group agrees
on pods, save them so the site shows exactly that seating and offers the
report form:

```bash
python scripts/pods.py --date 2026-11-08 --local --save
```

Each saved pod on the site has a **Report result** form: winner (or draw),
each seat's deck and decklist (filled in from their profile) and a note per
player, protected by the group PIN. Submissions go to the group's Google
Apps Script (`apps-script/`). The *Import pod results* workflow runs every 30
minutes, records them as games (turn order comes from the saved seating), and
rebuilds the site. To import right away: `python scripts/import_results.py`
(or run the workflow). Results reported to you in chat still go through
`log-game` as before. Include `--pod` and `--note-for` when you have them.

## 6. The group website

The group has a public site at https://ejw179.github.io/cedh-playgroup, built
by `scripts/build_site.py`. It shows next session's pods with a per-player
why, the meta (local, big events, global), player profiles and practice
standings. A GitHub Action in the repo
(`.github/workflows/site.yml`) rebuilds it nightly after syncing TopDeck,
and on every push that touches `data/` or the scripts.

So **after you change group data** (log games, set a session, add players,
change decks), the site doesn't update until `data/` is committed and pushed.
Offer it: "Want me to push this so the site updates?" On yes, `git pull
--rebase` first (the import bot commits results to `data/` too), then commit
only `data/*.json` with a short message ("Log 11/2 practice games") and push. The
cache and outbox are git-ignored. Remember the site is public: names, decks
and records are visible to anyone with the link. Never put anything private
in notes. To preview locally: `python scripts/build_site.py --out site`.

Players can also set their own practice decks on the site: each player card
has **Set practice decks**, with three dropdowns (their own decks first, then
every commander seen on TopDeck) and optional decklist links. The import job
applies it to the roster (`import_results.py`, latest request per player
wins; an empty set clears them).

### Scenarios: the reason for the pods

Every session is built for a reason. `pods.py --scenario NAME` (or the site's
picker) sets everything else. Scenarios play from each player's 1–3 practice
decks, or their recorded decks if they haven't set any:

| Scenario | Flag | What it builds |
|---|---|---|
| Improve one player | `improve --focus P` | P in their worst seat on their #1 deck, at a table of the decks they do worst against; others built around P |
| Worst-seat practice | `worst_seat` | Everyone on #1, in the seat where their record is weakest vs that seat's average |
| Tournament rehearsal | `rehearsal` | Everyone on #1 in their best seat; pods as even as possible |
| Face the local meta | `local` | Deck choices that put the most of the local meta at the tables; **gaps** called out |
| Face big-event meta | `big` | Each player's best big-event deck, worst seats; **major gaps** called out |
| Fix a matchup | `fix --fix P=CMDR` | That deck (someone's deck or a gauntlet deck) at P's table |
| Specific deck and seat | `specific --deck P=CMDR --seat P=N` | Locks what you choose; the rest is automatic |
| Generic practice | `generic --manual '[[{"player":..,"commander":..},..]]'` | Exactly the seating given; no analysis |
| Random | `random` | Random pods, seats, and decks from each player's 1–3 |

When explaining gaps, name the strongest missing deck and suggest a gauntlet
copy (`playgroup.py gauntlet-deck add`). Generic and random sessions get no
"why" and no Claude briefing. Don't add reasons to them.

**Where finished pods go:** the site's **History** section archives every
past session with its scenario, seating, each pod's results, notes and
decklists, plus practice standings. A pod shows **complete** once a result is
reported.

### Planning a session on the site

The site's **Plan a session** form (top of *Next session*) lets anyone with
the PIN pick a date, tick who's playing, choose seats (practise weak seats,
rehearse best seats, or random), choose the meta to face (local, big events,
global, or none), and optionally pick focus players. The request goes to the
same Apps Script as results. The *Import pod results* workflow (every 10
minutes) runs `scripts/plan_sessions.py`, which records availability, builds
the pods with the same logic as `pods.py`, and saves them. Planning the same
date again replaces its pods. The site lists every upcoming saved session.
When someone asks you in chat to change pods that were planned on the site,
re-plan with `pods.py --save` and say what changed. Don't silently re-seat
people who've already seen their pod.

### Claude's notes on the site

`scripts/ai_notes.py` asks Claude (Opus 5.5) for short written analysis
during each site build: analyst notes for each meta lens, a coach's note and
next step for each player (tied to their takeaways), and a briefing for each
saved pod. It sends only the numbers the site already shows. It skips the
call when the data hasn't changed. If the key is missing or the call fails,
the site builds without the notes. It runs in GitHub Actions with the
`ANTHROPIC_API_KEY` repo secret and costs about $0.10 per regeneration. To
look at notes without publishing, run the *Preview Claude notes* workflow and
download its artifact. These notes are a second opinion on the same data: in
chat, you still read the scripts' output yourself rather than quoting the
site's notes.

## Things to get right

- Players are identified by TopDeck ID, never by display name alone.
- Keep the TopDeck attribution line on anything using tournament data.
- Write to the data files only through the scripts, so the JSON stays
  consistent. Reading them directly is fine.
- Record the user's input faithfully. If a commander match looks wrong,
  check before saving.
