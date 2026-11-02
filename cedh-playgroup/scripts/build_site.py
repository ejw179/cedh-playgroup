"""Build the group's static website from the same data the skill uses.

  python build_site.py --out ../../site

Writes one self-contained index.html (no external assets): next session's
pods with a per-player "why", the meta through three lenses (local, big
events, global), player profiles, and practice standings. The nightly
GitHub Action runs sync.py then this, and publishes the folder to Pages.
"""
import argparse
import html
import json
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import pods as podmod
from analyze import (Profiler, area_label, big_min, meta, player_report, practice_stats, short,
                     takeaways, tournaments)
from common import ATTRIBUTION, data_dir, load

REPO_URL = "https://github.com/ejw179/cedh-playgroup"
e = html.escape


def pct(x, digits=1):
    return "–" if x is None else f"{x * 100:.{digits}f}%"


def pts(x):
    return f"{x * 100:+.1f} pts"


def deck_link(commander: str, url: str | None) -> str:
    name = e(short(commander))
    return f'<a href="{e(url)}" rel="noopener">{name}</a>' if url else name


# ---------- data ----------

def meta_views(cfg: dict) -> list[dict]:
    """The three lenses: local (home radius, all sizes, 30d), big (101+, 90d), global (16+, 30d)."""
    views = []
    home = cfg.get("home")
    if home:
        views.append({"id": "local", "label": "Local", "desc": f"Events {area_label(home)}, every size, last 30 days",
                      "days": 30, "min": 0, "area": home})
    big = big_min()
    views.append({"id": "big", "label": "Big events", "desc": f"Events with {big}+ players, anywhere, last 90 days",
                  "days": 90, "min": big, "area": None})
    views.append({"id": "global", "label": "Global", "desc": "All TopDeck events with 16+ players, last 30 days",
                  "days": 30, "min": 16, "area": None})
    for v in views:
        v["now"] = meta(tournaments(v["days"], v["min"], area=v["area"]))
        v["prev"] = meta(tournaments(v["days"], v["min"], offset_days=v["days"], area=v["area"]))
    return views


def upcoming_sessions() -> list[str]:
    """Every saved session from today on (soonest first), else the most recent one."""
    sessions = sorted(load("availability.json")["sessions"])
    upcoming = [d for d in sessions if d >= date.today().isoformat()]
    return upcoming or sessions[-1:]


def build_pods(day: str, cfg: dict) -> dict | None:
    """The locked-in seating for that session if it was saved, else a draft."""
    if not day:
        return None
    saved = load("pods.json")["sessions"].get(day)
    if saved:
        return {**saved, "draft": False}
    args = ["--date", day, "--json"] + (["--local"] if cfg.get("home") else [])
    try:
        _, result = podmod.plan(podmod.parser().parse_args(args))
    except SystemExit:  # e.g. fewer than 3 players saved for that day
        return None
    return {**result, "date": day, "draft": True}


def seat_why(s: dict) -> list[str]:
    """Plain-English reasons for one seat, built from the pod builder's evidence."""
    out = []
    ev = s["evidence"]
    if not ev["tournament_games"] and not ev["practice_games"]:
        return ["No TopDeck or practice data yet. Practice reports will fill this in."]
    rec, vs = s["seat_record"], s["seat_vs_field"]
    if s["seat_read"] == "worst seat":
        out.append(f"Seat {s['seat']} is their worst seat ({rec}, {pts(vs)} vs what seat {s['seat']} usually wins), "
                   "so they practise it here.")
    elif s["seat_read"] == "best seat":
        out.append(f"Seat {s['seat']} is their best seat ({rec}).")
    elif s["seat_read"] == "no strong read":
        out.append(f"No strong read in seat {s['seat']} yet ({rec}).")
    else:
        out.append(f"First tracked games in seat {s['seat']}.")
    for h in s["matchups_here"][:3]:
        verb = "Needs reps vs" if h["gap"] < 0 else "Favoured vs"
        out.append(f"{verb} {h['opponent']}'s {short(h['commander'])} ({'; '.join(h['evidence'][:2])}).")
    for w in s["needs_practice_elsewhere"][:1]:
        out.append(f"Also weak vs {short(w['commander'])}, not at this table ({'; '.join(w['evidence'][:1])}).")
    if s["gauntlet"]:
        out.append("Piloting a shared gauntlet deck.")
    if s.get("practice_rank"):
        out.append(f"Their #{s['practice_rank']} practice deck.")
    t = s.get("takeaway")
    if t:
        out.append(f"Working on (from {t['date']}): “{t['text']}”")
    return out


def game_card(g: dict) -> str:
    """One reported game: turn order, winner, decklists and each player's note."""
    lists, notes = g.get("decklists", {}), g.get("notes", {})
    rows = []
    for i, s in enumerate(g["seats"], 1):
        won = g["winner"] == s["player"]
        deck = e(short(s["commander"]))
        if lists.get(s["player"]):
            deck = f'<a href="{e(lists[s["player"]])}" rel="noopener">{deck}</a>'
        note = f'<div class="note-line">“{e(notes[s["player"]])}”</div>' if notes.get(s["player"]) else ""
        rows.append(f'<li class="{"won" if won else ""}"><span class="seat-n sm">{i}</span><div>'
                    f'<strong>{e(s["player"])}</strong> {deck}{" <span class=tag>winner</span>" if won else ""}{note}</div></li>')
    head = "Draw" if g["winner"] is None else f'{e(g["winner"])} won'
    by = f' · reported by {e(g["reported_by"])}' if g.get("reported_by") else ""
    pod = f' · pod {g["pod"]}' if g.get("pod") else ""
    return (f'<div class="game"><div class="game-head"><strong>{head}</strong>'
            f'<span class="muted small">{e(g["date"])}{pod}{by}</span></div>'
            f'<ol class="game-seats">{"".join(rows)}</ol></div>')


def report_form(day: str, pod: dict, results_url: str) -> str:
    """'Close the pod': winner, each seat's deck + decklist + note, group PIN."""
    if not results_url:
        return '<p class="muted small">Online reporting isn’t set up yet. Send results to Claude instead.</p>'
    seats = "".join(
        f'<fieldset class="rf-seat" data-player="{e(s["player"])}"><legend>Seat {s["seat"]}: {e(s["player"])}</legend>'
        f'<label>Deck <input name="commander" value="{e(s["commander"])}" required></label>'
        f'<label>Decklist link <input name="decklist" type="url" placeholder="https://moxfield.com/decks/…" '
        f'value="{e(s.get("decklist") or "")}"></label>'
        f'<label>Note / takeaway <textarea name="note" rows="2" maxlength="600" '
        f'placeholder="What did you learn? What will you work on?"></textarea></label></fieldset>'
        for s in pod["seats"])
    winners = "".join(f'<label class="chip"><input type="radio" name="winner" value="{e(s["player"])}" required> '
                      f'{e(s["player"])}</label>' for s in pod["seats"])
    winners += '<label class="chip"><input type="radio" name="winner" value="draw"> Draw</label>'
    return (f'<details class="report"><summary>Report result</summary>'
            f'<form class="rf" data-date="{e(day)}" data-pod="{pod["pod"]}">'
            f'<div class="rf-row"><span class="rf-label">Winner</span><div class="chips">{winners}</div></div>'
            f'{seats}'
            f'<div class="rf-row two"><label>Your name <input name="reporter" maxlength="60"></label>'
            f'<label>Group PIN <input name="pin" type="password" autocomplete="off" required></label></div>'
            f'<p class="muted small">Seats are already in turn order. Report each game separately.</p>'
            f'<button type="submit">Submit result</button> <span class="rf-status" role="status"></span>'
            f'</form></details>')


# ---------- rendering ----------

def load_ai() -> dict | None:
    """Claude's notes from ai_notes.py, if they've been generated."""
    path = data_dir() / "ai_notes.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, json.JSONDecodeError):
        return None


def ai_block(title: str, body: str, ai: dict) -> str:
    when = (ai.get("generated_at") or "")[:10]
    return (f'<div class="ai"><div class="ai-head"><strong>{e(title)}</strong>'
            f'<span class="muted small">Written by Claude from these stats · {e(when)}</span></div>{body}</div>')


def plan_form(roster: dict, results_url: str) -> str:
    """'Plan a session': date, who's playing, scenario. The import job builds and saves the pods."""
    if not results_url:
        return ""
    names = [p["name"] for p in roster["players"]]
    boxes = "".join(f'<label class="chip"><input type="checkbox" name="players" value="{e(n)}"> {e(n)}</label>'
                    for n in names)
    focus = "".join(f'<label class="chip"><input type="checkbox" name="focus" value="{e(n)}"> {e(n)}</label>'
                    for n in names)

    def radios(name, opts, default):
        return "".join(f'<label class="chip"><input type="radio" name="{name}" value="{v}"'
                       f'{" checked" if v == default else ""}> {e(t)}</label>' for v, t in opts)

    seats = radios("seats", [("practice", "Practise weak seats"), ("best", "Rehearse best seats"),
                             ("random", "Random seats")], "practice")
    meta = radios("meta", [("local", "Local meta"), ("big", "Big events"), ("global", "Global"),
                           ("none", "Don’t weight meta")], "local")
    return (f'<details class="report plan"><summary>Plan a session</summary>'
            f'<form class="pf">'
            f'<div class="rf-row two"><label>Date <input name="date" type="date" required></label>'
            f'<label>Your name <input name="reporter" maxlength="60"></label></div>'
            f'<div class="rf-row"><span class="rf-label">Who’s playing (3 or more)</span><div class="chips">{boxes}</div></div>'
            f'<div class="rf-row"><span class="rf-label">Seats</span><div class="chips">{seats}</div></div>'
            f'<div class="rf-row"><span class="rf-label">Meta to face</span><div class="chips">{meta}</div></div>'
            f'<div class="rf-row"><span class="rf-label">Focus on weak matchups for (optional)</span>'
            f'<div class="chips">{focus}</div></div>'
            f'<label>Group PIN <input name="pin" type="password" autocomplete="off" required></label>'
            f'<p class="muted small">Pods are built with each player’s practice decks and seat data, then appear '
            f'here within about 10 minutes. Planning the same date again replaces its pods.</p>'
            f'<button type="submit">Build pods</button> <span class="rf-status" role="status"></span>'
            f'</form></details>')


def render_pods(p: dict | None, games: list[dict], results_url: str, ai: dict | None = None) -> str:
    if not p:
        return ('<p class="empty">No session saved yet. Use <strong>Plan a session</strong> above, or tell Claude '
                'who\'s playing ("everyone except Jeff on Saturday").</p>')
    dt = datetime.fromisoformat(p["date"])
    d = f"{dt:%a %b} {dt.day}"  # %-d isn't portable to Windows
    seats_avg = " · ".join(f"seat {i + 1}: {pct(f)}" for i, f in enumerate(p["field_seats"]))
    draft = ('<p class="note">Draft seating, not locked in yet. Plan it above (or ask Claude to save it) '
             'to lock it in.</p>' if p.get("draft") else "")
    how = {"practice": "each player sits where their record is weakest compared with what that seat normally wins",
           "best": "each player sits in their strongest seat (tournament rehearsal)",
           "random": "seats are shuffled"}.get(p.get("seat_mode", "practice"), "")
    sc = p.get("scenario") or {}
    plan = ""
    if sc:
        lens = {"local": "local meta", "big": "big-event meta", "global": "global meta", "none": "no meta weighting"}
        plan = (f' Planned{" by " + e(p["requested_by"]) if p.get("requested_by") else ""}: '
                f'{e(lens.get(sc.get("meta"), sc.get("meta", "")))}'
                + (f', focus on {e(", ".join(sc["focus"]))}' if sc.get("focus") else "") + ".")
    parts = [f'<h3 class="session-h">{e(d)}</h3>'
             f'<p class="lede">Seat = turn order; {e(how)} ({e(seats_avg)}).{plan}</p>{draft}']
    for pod in p["pods"]:
        cards = []
        for s in pod["seats"]:
            # saved pods keep the seating; the takeaway shown is always the player's latest
            live = (takeaways(games, s["player"], 1) or [s.get("takeaway")])[0]
            why = "".join(f"<li>{e(w)}</li>" for w in seat_why({**s, "takeaway": live}))
            tag = '<span class="tag">gauntlet</span>' if s["gauntlet"] else ""
            deck = e(short(s["commander"]))
            if s.get("decklist"):
                deck = f'<a href="{e(s["decklist"])}" rel="noopener">{deck}</a>'
            cards.append(f'<li class="seat"><div class="seat-n">{s["seat"]}</div><div class="seat-body">'
                         f'<div class="seat-head"><strong>{e(s["player"])}</strong> '
                         f'<span class="deck">{deck}</span>{tag}</div>'
                         f'<ul class="why">{why}</ul></div></li>')
        meta_line = (f'<p class="muted small">Meta decks at the table: '
                     f'{e("; ".join(m.split(" (")[0] for m in pod["meta_decks"]))}</p>' if pod["meta_decks"] else "")
        done = [g for g in games if g["date"] == p["date"] and g.get("pod") == pod["pod"]]
        results = ("".join(game_card(g) for g in done) if done else "")
        results_block = f'<div class="results"><h4>Results</h4>{results}</div>' if done else ""
        form = "" if p.get("draft") else report_form(p["date"], pod, results_url)
        brief = ""
        if ai and ai.get("session") == p["date"]:
            text = next((b["briefing"] for b in ai.get("pods", []) if b.get("pod") == pod["pod"]), "")
            brief = ai_block("Briefing", f"<p>{e(text)}</p>", ai) if text else ""
        parts.append(f'<article class="card pod"><h3>Pod {pod["pod"]}</h3>{brief}<ol class="seats">{"".join(cards)}</ol>'
                     f'{meta_line}{results_block}{form}</article>')
    if p["bench"]:
        parts.append(f'<p class="muted">Sitting out first game: {e(", ".join(p["bench"]))}. Rotate them in next.</p>')
    parts.append('<p class="muted small">Results reported here show up within about 30 minutes, and each '
                 'player’s note becomes their takeaway for the next pods.</p>')
    return "".join(parts)


def owners_by_commander(roster: dict) -> dict:
    own = defaultdict(list)
    for p in roster["players"]:
        for d in p.get("decks", []):
            own[d["commander"]].append(p["name"])
    for d in roster.get("gauntlet_decks", []):
        own[d["commander"]].append("gauntlet")
    return own


def render_meta(views: list[dict], roster: dict, ai: dict | None = None) -> str:
    own = owners_by_commander(roster)
    tabs, panels = [], []
    for i, v in enumerate(views):
        m, prev = v["now"], v["prev"]
        rows = sorted(m["commanders"].items(), key=lambda kv: -kv[1]["entries"])[:20]
        top_share = rows[0][1]["share"] if rows else 1
        trs = []
        for n, (c, r) in enumerate(rows, 1):
            ps = prev["commanders"].get(c, {}).get("share") if prev["total_entries"] else None
            delta = "" if ps is None else f'<span class="{"up" if r["share"] > ps else "down" if r["share"] < ps else ""}">{(r["share"] - ps) * 100:+.1f}</span>'
            bar = f'<span class="bar" style="width:{r["share"] / top_share * 100:.0f}%"></span>'
            who = ", ".join(own.get(c, []))
            trs.append(f'<tr><td class="num">{n}</td><td><span class="cmdr">{e(short(c))}</span>'
                       f'{f"<span class=owner>{e(who)}</span>" if who else ""}</td>'
                       f'<td class="num">{r["entries"]}</td><td class="share">{bar}<span>{pct(r["share"])}</span></td>'
                       f'<td class="num">{delta}</td><td class="num">{pct(r["win_rate"])}</td>'
                       f'<td class="num">{pct(r["conversion"], 0)}</td></tr>')
        seats = sum(r["wins"] + r["draws"] + r["losses"] for r in m["commanders"].values())
        avg = sum(r["wins"] for r in m["commanders"].values()) / seats if seats else 0.25
        small = ('<p class="note">Small sample: read shares as who shows up, and win rates as hints.</p>'
                 if m["total_entries"] < 150 else "")
        notes = (ai or {}).get("meta", {}).get(v["id"]) or []
        analyst = (ai_block("Analyst notes", "<ul>" + "".join(f"<li>{e(n)}</li>" for n in notes) + "</ul>", ai)
                   if notes else "")
        tabs.append(f'<button role="tab" aria-selected="{"true" if i == 0 else "false"}" data-tab="{v["id"]}">{e(v["label"])}</button>')
        panels.append(
            f'<div role="tabpanel" id="tab-{v["id"]}" {"" if i == 0 else "hidden"}>'
            f'<p class="muted">{e(v["desc"])}: {m["events"]} events, {m["total_entries"]} entries. '
            f'Average seat wins {pct(avg)}, so read win rates against that, not 50%.</p>{small}{analyst}'
            f'<div class="scroll"><table><thead><tr><th>#</th><th>Commander <span class="muted">· who plays it here</span></th>'
            f'<th>Entries</th><th>Share</th><th>Δ</th><th>Win rate</th><th>Top cut</th></tr></thead>'
            f'<tbody>{"".join(trs) or "<tr><td colspan=7>No events in this window.</td></tr>"}</tbody></table></div></div>')
    return f'<div class="tabs" role="tablist">{"".join(tabs)}</div>{"".join(panels)}'


def render_players(roster: dict, prof: Profiler, games: list[dict], ai: dict | None = None) -> str:
    coach = {c.get("name"): c for c in (ai or {}).get("players", [])}
    reports = {r["name"]: r for r in player_report([], 60)}
    cards = []
    for p in roster["players"]:
        pf = prof.profile(p)
        rep = reports.get(p["name"], {})
        t = rep.get("tournament", {})
        link = (f'<a href="https://topdeck.gg/profile/{e(p["topdeck_id"])}">{e(p.get("topdeck_name") or "TopDeck")}</a>'
                if p.get("topdeck_id") else '<span class="muted">no TopDeck link</span>')
        decks = ", ".join(short(d["commander"]) + (" (main)" if d.get("primary") else "") for d in p.get("decks", []))
        form = (f'{t["events"]} events · {t["wins"]}-{t["losses"]}-{t["draws"]} · {pct(t["win_rate"])} · '
                f'{t["top_cuts"]} top cut{"s" if t["top_cuts"] != 1 else ""}') if t.get("events") else "No tracked events in 60 days"
        seat_cells = []
        for s in pf["seats"]:
            cls = ""
            if s["seat"] == pf["best_seat"]:
                cls = "best"
            elif s["seat"] == pf["worst_seat"]:
                cls = "worst"
            rec = f'{s["wins"]}/{s["games"]}' if s["games"] else "–"
            seat_cells.append(f'<div class="cell {cls}" title="seat {s["seat"]}: {pts(s["vs_field"])} vs seat average">'
                              f'<span class="k">Seat {s["seat"]}</span><span class="v">{rec}</span></div>')

        def mlist(rows, label, cls):
            if not rows:
                return ""
            items = "".join(f'<li>{e(short(r["theirs"]))} <span class="muted">({e("; ".join(r["evidence"][:1]))})</span></li>'
                            for r in rows[:3])
            return f'<div class="{cls}"><h4>{label}</h4><ul>{items}</ul></div>'

        mu = mlist(pf["weak"], "Needs practice vs", "weak") + mlist(pf["strong"], "Strong vs", "strong")
        if not mu:
            mu = '<p class="muted small">No matchup has 3+ games of evidence yet.</p>'
        practice = ""
        if p.get("practice_decks"):
            items = "".join(f'<li>{deck_link(d["commander"], d.get("list"))}</li>' for d in p["practice_decks"])
            practice = f'<div class="practice"><h4>Practising</h4><ol>{items}</ol></div>'
        tks = takeaways(games, p["name"], 2)
        take = ""
        if tks:
            take = '<div class="takeaways"><h4>Takeaways</h4>' + "".join(
                f'<p class="small">“{e(t["text"])}” <span class="muted">{e(t["date"])}, on {e(t["commander"])}, '
                f'{e(t["result"])}</span></p>' for t in tks) + "</div>"
        cards.append(f'<article class="card player"><header><h3>{e(p["name"])}</h3>{link}</header>'
                     f'<p class="deck">{e(decks) or "No decks recorded"}</p><p class="muted small">{e(form)}</p>'
                     f'{practice}{take}'
                     + (ai_block("Coach’s note", f'<p>{e(coach[p["name"]]["note"])}</p>'
                                 f'<p class="next"><strong>Next:</strong> {e(coach[p["name"]]["next_step"])}</p>', ai)
                        if p["name"] in coach else "") +
                     f'<div class="seatstrip">{"".join(seat_cells)}</div><div class="mu">{mu}</div></article>')
    return f'<div class="grid">{"".join(cards)}</div>'


def render_practice(games: list[dict]) -> str:
    if not games:
        return ('<p class="empty">No practice games logged yet. Send results to Claude in turn order, '
                'e.g. <code>G1: Alex Tayam, Darison Sisay, Eric Kinnan, Ryan Tymna/Kraum. Winner Darison</code>.</p>')
    st = practice_stats(games)
    rows = sorted(st.items(), key=lambda kv: (-(kv[1]["wins"] / kv[1]["games"]), -kv[1]["games"]))
    trs = "".join(f'<tr><td>{e(n)}</td><td class="num">{s["games"]}</td><td class="num">{s["wins"]}</td>'
                  f'<td class="num">{s["draws"]}</td><td class="num">{pct(s["wins"] / s["games"], 0)}</td>'
                  f'<td>{e(", ".join(f"{short(c)} {s["wins_by_commander"][c]}/{k}" for c, k in s["by_commander"].most_common(2)))}</td></tr>'
                  for n, s in rows)
    by_day = defaultdict(list)
    for g in games:
        by_day[g["date"]].append(g)
    sessions = []
    for day in sorted(by_day, reverse=True)[:6]:
        cards = "".join(game_card(g) for g in sorted(by_day[day], key=lambda g: g.get("pod") or 0))
        sessions.append(f'<details class="session" {"open" if not sessions else ""}><summary>{e(day)} '
                        f'<span class="muted small">{len(by_day[day])} game{"s" if len(by_day[day]) != 1 else ""}</span>'
                        f'</summary><div class="game-grid">{cards}</div></details>')
    return (f'<div class="scroll"><table><thead><tr><th>Player</th><th>Games</th><th>Wins</th><th>Draws</th>'
            f'<th>Win %</th><th>Decks</th></tr></thead><tbody>{trs}</tbody></table></div>'
            f'<p class="muted small">In 4-player games about 20–25% is average.</p>'
            f'<h3>Pod results</h3>{"".join(sessions)}')


CSS = """
:root{--bg:#f7f6f3;--surface:#fff;--ink:#1d1b18;--muted:#6b665e;--line:#e4e0d8;--accent:#6b3fd1;--accent-ink:#fff;
--good:#1f7a4a;--bad:#b4372b;--bar:#d9cff5;--good-bg:#e3f3ea;--bad-bg:#f8e3e0}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#141311;--surface:#1d1c19;--ink:#ece8e1;
--muted:#a39d93;--line:#2f2d29;--accent:#a98bf2;--accent-ink:#141311;--good:#5fc792;--bad:#ef7d70;--bar:#3a3159;
--good-bg:#173226;--bad-bg:#3a1d19}}
:root[data-theme="dark"]{--bg:#141311;--surface:#1d1c19;--ink:#ece8e1;--muted:#a39d93;--line:#2f2d29;--accent:#a98bf2;
--accent-ink:#141311;--good:#5fc792;--bad:#ef7d70;--bar:#3a3159;--good-bg:#173226;--bad-bg:#3a1d19}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:var(--accent)}code{font-size:.85em;background:var(--line);padding:.1em .35em;border-radius:4px;word-break:break-word}
.wrap{max-width:1080px;margin:0 auto;padding:0 16px}
header.top{border-bottom:1px solid var(--line);background:var(--surface)}
header.top .wrap{display:flex;flex-wrap:wrap;align-items:baseline;gap:.25rem 1rem;padding-top:18px;padding-bottom:14px}
header.top h1{font-size:1.35rem;margin:0;letter-spacing:-.01em}
nav{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line)}
nav .wrap{display:flex;gap:1rem;overflow-x:auto;padding-top:10px;padding-bottom:10px}
nav a{color:var(--ink);text-decoration:none;white-space:nowrap;font-weight:600;font-size:.95rem}
nav a:hover{color:var(--accent)}
section{padding:28px 0 8px}section h2{font-size:1.2rem;margin:0 0 .75rem}
.muted{color:var(--muted)}.small{font-size:.85rem}.lede{max-width:70ch}
.empty{padding:1rem;border:1px dashed var(--line);border-radius:10px;color:var(--muted)}
.note{font-size:.85rem;color:var(--muted);border-left:3px solid var(--accent);padding-left:.6rem}
.card{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:14px}
.card h3{margin:0 0 .5rem;font-size:1.05rem}
.seats{list-style:none;margin:0;padding:0;display:grid;gap:10px}
@media (min-width:760px){.seats{grid-template-columns:1fr 1fr}}
.seat{display:flex;gap:10px;border:1px solid var(--line);border-radius:10px;padding:10px}
.seat-n{flex:none;width:28px;height:28px;border-radius:50%;background:var(--accent);color:var(--accent-ink);
display:grid;place-items:center;font-weight:700}
.seat-body{min-width:0}.deck{color:var(--muted)}.why{margin:.35rem 0 0;padding-left:1.1rem;font-size:.9rem}
.tag{display:inline-block;font-size:.72rem;font-weight:600;padding:.05rem .4rem;border-radius:999px;
background:var(--bar);margin-left:.35rem;vertical-align:middle}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}
.tabs button{font:inherit;font-weight:600;border:1px solid var(--line);background:var(--surface);color:var(--ink);
padding:.35rem .8rem;border-radius:999px;cursor:pointer}
.tabs button[aria-selected="true"]{background:var(--accent);color:var(--accent-ink);border-color:var(--accent)}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;border:1px solid var(--line);border-radius:12px;background:var(--surface)}
table{border-collapse:collapse;width:100%;font-size:.9rem}
th,td{padding:.5rem .6rem;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{font-size:.78rem;text-transform:uppercase;letter-spacing:.03em;color:var(--muted);white-space:nowrap}
tr:last-child td{border-bottom:0}td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.cmdr{font-weight:600}.owner{display:block;font-size:.78rem;color:var(--accent)}
td.share{min-width:96px;font-variant-numeric:tabular-nums}
td.share .bar{display:block;height:6px;margin:4px 0 0;background:var(--bar);border-radius:3px;order:2}
td.share{display:flex;flex-direction:column}
.up{color:var(--good)}.down{color:var(--bad)}
.grid{display:grid;gap:14px}@media (min-width:700px){.grid{grid-template-columns:1fr 1fr}}
@media (min-width:1000px){.grid{grid-template-columns:1fr 1fr 1fr}}
.player header{display:flex;justify-content:space-between;align-items:baseline;gap:.5rem;flex-wrap:wrap}
.player .deck{margin:.1rem 0}.player p{margin:.2rem 0}
.seatstrip{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin:.6rem 0}
.cell{border:1px solid var(--line);border-radius:8px;padding:.3rem .4rem;text-align:center}
.cell .k{display:block;font-size:.7rem;color:var(--muted)}.cell .v{font-weight:700;font-variant-numeric:tabular-nums}
.cell.best{background:var(--good-bg);border-color:var(--good)}.cell.worst{background:var(--bad-bg);border-color:var(--bad)}
.mu h4{margin:.4rem 0 .15rem;font-size:.8rem;text-transform:uppercase;letter-spacing:.03em}
.mu .weak h4{color:var(--bad)}.mu .strong h4{color:var(--good)}.mu ul{margin:0;padding-left:1.1rem;font-size:.88rem}
.games{padding-left:1.3rem;font-size:.9rem}.games li{margin:.25rem 0}
footer{margin-top:28px;border-top:1px solid var(--line);padding:18px 0 40px;font-size:.85rem;color:var(--muted)}
.player h4,.results h4{margin:.5rem 0 .15rem;font-size:.8rem;text-transform:uppercase;letter-spacing:.03em;color:var(--muted)}
.practice ol{margin:0;padding-left:1.2rem;font-size:.9rem}.takeaways p{margin:.15rem 0}
.game{border:1px solid var(--line);border-radius:10px;padding:10px;margin:.5rem 0;background:var(--surface)}
.game-head{display:flex;justify-content:space-between;gap:.5rem;flex-wrap:wrap;margin-bottom:.35rem}
.game-seats{list-style:none;margin:0;padding:0;display:grid;gap:6px}
.game-seats li{display:flex;gap:8px;align-items:flex-start;font-size:.9rem}
.game-seats li.won strong{color:var(--good)}.note-line{color:var(--muted);font-size:.85rem}
.seat-n.sm{width:20px;height:20px;font-size:.72rem;flex:none}
.game-grid{display:grid;gap:10px}@media (min-width:760px){.game-grid{grid-template-columns:1fr 1fr}}
.session{margin:.5rem 0}.session summary,.report summary{cursor:pointer;font-weight:600}
.report{margin-top:12px;border-top:1px solid var(--line);padding-top:10px}
.rf{display:grid;gap:10px;margin-top:10px}.rf label{display:grid;gap:3px;font-size:.85rem;color:var(--muted)}
.rf input,.rf textarea{font:inherit;color:var(--ink);background:var(--bg);border:1px solid var(--line);
border-radius:8px;padding:.45rem .55rem;width:100%}
.rf input:focus,.rf textarea:focus{outline:2px solid var(--accent);outline-offset:1px}
.rf fieldset{border:1px solid var(--line);border-radius:10px;padding:8px 10px;display:grid;gap:6px;margin:0}
.rf legend{font-weight:600;padding:0 .3rem}.rf-row{display:grid;gap:6px}.rf-row.two{grid-template-columns:1fr 1fr}
.rf-label{font-size:.85rem;color:var(--muted)}.chips{display:flex;flex-wrap:wrap;gap:6px}
.rf .chip{display:inline-flex;align-items:center;gap:.35rem;border:1px solid var(--line);border-radius:999px;
padding:.3rem .7rem;color:var(--ink);font-size:.9rem;cursor:pointer}.rf .chip input{width:auto}
.rf button{font:inherit;font-weight:700;background:var(--accent);color:var(--accent-ink);border:0;border-radius:999px;
padding:.55rem 1.1rem;cursor:pointer;justify-self:start}.rf button[disabled]{opacity:.6;cursor:default}
.ai{border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:10px;padding:10px 12px;
margin:.6rem 0;background:var(--surface)}.ai-head{display:flex;justify-content:space-between;gap:.5rem;flex-wrap:wrap}
.ai ul{margin:.35rem 0 0;padding-left:1.1rem;font-size:.92rem}.ai li{margin:.2rem 0}.ai p{margin:.35rem 0 0;font-size:.92rem}
.ai .next{color:var(--ink)}
.session-h{margin:1.4rem 0 .2rem;font-size:1.1rem}.plan{margin-bottom:.5rem}
.rf-status{font-size:.9rem}.rf-status.ok{color:var(--good)}.rf-status.err{color:var(--bad)}
"""

JS = """
document.querySelectorAll('.tabs').forEach(function(bar){
  bar.addEventListener('click',function(ev){
    var b=ev.target.closest('button[data-tab]'); if(!b) return;
    bar.querySelectorAll('button').forEach(function(x){x.setAttribute('aria-selected',x===b?'true':'false')});
    document.querySelectorAll('[role=tabpanel]').forEach(function(p){p.hidden=p.id!=='tab-'+b.dataset.tab});
  });
});

// "Report result": post one game to the group's Apps Script endpoint.
var RESULTS_URL = document.body.dataset.resultsUrl;
function store(k, v){ try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } }
document.querySelectorAll('form.rf').forEach(function(f){
  var pin = f.querySelector('[name=pin]'), who = f.querySelector('[name=reporter]');
  pin.value = store('cedh-pin') || ''; who.value = store('cedh-reporter') || '';
  f.addEventListener('submit', function(ev){
    ev.preventDefault();
    var status = f.querySelector('.rf-status'), btn = f.querySelector('button');
    var winner = f.querySelector('[name=winner]:checked');
    if (!winner) { status.className = 'rf-status err'; status.textContent = 'Pick the winner (or Draw).'; return; }
    var seats = Array.prototype.map.call(f.querySelectorAll('.rf-seat'), function(fs, i){
      return { seat: i + 1, player: fs.dataset.player,
               commander: fs.querySelector('[name=commander]').value.trim(),
               decklist: fs.querySelector('[name=decklist]').value.trim(),
               note: fs.querySelector('[name=note]').value.trim() };
    });
    var body = { type: 'result', date: f.dataset.date, pod: Number(f.dataset.pod), winner: winner.value, seats: seats,
                 reporter: who.value.trim(), pin: pin.value.trim() };
    btn.disabled = true; status.className = 'rf-status'; status.textContent = 'Sending…';
    fetch(RESULTS_URL, { method: 'POST', body: JSON.stringify(body) })
      .then(function(r){ return r.json(); })
      .then(function(res){
        if (!res.ok) throw new Error(res.error || 'Not saved.');
        store('cedh-pin', body.pin); store('cedh-reporter', body.reporter);
        status.className = 'rf-status ok';
        status.textContent = 'Saved. It will appear here within about 30 minutes. Report the next game the same way.';
        f.querySelectorAll('[name=note]').forEach(function(t){ t.value = ''; });
        f.querySelectorAll('[name=winner]').forEach(function(r){ r.checked = false; });
      })
      .catch(function(err){ status.className = 'rf-status err'; status.textContent = 'Not saved: ' + err.message; })
      .then(function(){ btn.disabled = false; });
  });
});

// "Plan a session": who's playing + scenario; the import job builds and saves the pods.
document.querySelectorAll('form.pf').forEach(function(f){
  var pin = f.querySelector('[name=pin]'), who = f.querySelector('[name=reporter]');
  pin.value = store('cedh-pin') || ''; who.value = store('cedh-reporter') || '';
  function checked(name){ return Array.prototype.map.call(f.querySelectorAll('[name=' + name + ']:checked'), function(x){ return x.value; }); }
  f.addEventListener('submit', function(ev){
    ev.preventDefault();
    var status = f.querySelector('.rf-status'), btn = f.querySelector('button');
    var players = checked('players');
    if (players.length < 3) { status.className = 'rf-status err'; status.textContent = 'Pick at least 3 players.'; return; }
    var body = { type: 'session', date: f.querySelector('[name=date]').value, players: players,
                 seats: checked('seats')[0], meta: checked('meta')[0], focus: checked('focus'),
                 reporter: who.value.trim(), pin: pin.value.trim() };
    btn.disabled = true; status.className = 'rf-status'; status.textContent = 'Sending…';
    fetch(RESULTS_URL, { method: 'POST', body: JSON.stringify(body) })
      .then(function(r){ return r.json(); })
      .then(function(res){
        if (!res.ok) throw new Error(res.error || 'Not saved.');
        store('cedh-pin', body.pin); store('cedh-reporter', body.reporter);
        status.className = 'rf-status ok';
        status.textContent = 'Request saved. Pods for ' + body.date + ' will appear on this page within about 10 minutes.';
      })
      .catch(function(err){ status.className = 'rf-status err'; status.textContent = 'Not saved: ' + err.message; })
      .then(function(){ btn.disabled = false; });
  });
});
"""


def page(cfg: dict) -> str:
    roster = load("roster.json")
    games = load("games.json")["games"]
    prof = Profiler(30, 16)
    ai = load_ai()
    days = upcoming_sessions()
    sections = [
        ("pods", "Next session", plan_form(roster, cfg.get("results_url", ""))
         + ("".join(render_pods(build_pods(d, cfg), games, cfg.get("results_url", ""), ai) for d in days)
            or render_pods(None, games, cfg.get("results_url", ""), ai))),
        ("meta", "Meta", render_meta(meta_views(cfg), roster, ai)),
        ("players", "Players", render_players(roster, prof, games, ai)),
        ("practice", "Practice", render_practice(games)),
    ]
    nav = "".join(f'<a href="#{sid}">{e(t)}</a>' for sid, t, _ in sections)
    body = "".join(f'<section id="{sid}"><div class="wrap"><h2>{e(t)}</h2>{h}</div></section>' for sid, t, h in sections)
    home = cfg.get("home")
    sub = f"Home: {area_label(home)}" if home else ""
    stamp = datetime.now(timezone.utc).strftime("%b %d, %Y %H:%M UTC")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>cEDH Playgroup</title><meta name="description" content="Practice pods, local and big-event meta, and player profiles for our cEDH playgroup.">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%236b3fd1'/%3E%3Ctext x='16' y='22' font-size='16' text-anchor='middle' fill='white' font-family='sans-serif' font-weight='700'%3EC%3C/text%3E%3C/svg%3E">
<style>{CSS}</style></head><body data-results-url="{e(cfg.get('results_url', ''))}">
<header class="top"><div class="wrap"><h1>cEDH Playgroup</h1><span class="muted small">{e(sub)}</span></div></header>
<nav><div class="wrap">{nav}</div></nav>
<main>{body}</main>
<footer><div class="wrap"><p>Tournament data from <a href="https://topdeck.gg">TopDeck.gg</a>. Updated {e(stamp)}.</p>
<p>Win rates are four-player numbers: an average seat wins about 20% once draws are counted. Samples under ~10 games are hints, not findings.
Built by the <a href="{REPO_URL}">cedh-playgroup</a> skill.</p></div></footer>
<script>{JS}</script></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cfg = load("config.json")
    (out / "index.html").write_text(page(cfg), encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    print(f"Built {out / 'index.html'}")
    print(ATTRIBUTION)


if __name__ == "__main__":
    main()
