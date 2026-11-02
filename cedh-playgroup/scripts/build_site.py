"""Build the group's static website from the same data the skill uses.

  python build_site.py --out ../../site

One self-contained index.html, in four sections:
  Sessions  plan a session (scenario + players), upcoming sessions with pods,
            each seat's reason, gap call-outs, results and report forms
  Meta      local / big events / global, with Claude's analyst notes and gaps
  Players   profiles, seats, matchups, takeaways, coach's notes, practice decks
  History   standings and every past session: scenario, seating, results
The nightly GitHub Action runs sync.py then this, and publishes the folder to Pages.
"""
import argparse
import html
import json
import re
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import pods as podmod
from analyze import (Profiler, area_label, big_min, meta, player_report, practice_stats, short,
                     takeaways, tournaments)
from common import ATTRIBUTION, data_dir, load

REPO_URL = "https://github.com/ejw179/cedh-playgroup"
e = html.escape

SCENARIO_HELP = {
    "improve": "One player's improvement: their worst seat, the decks they do worst against, everyone else built around them.",
    "worst_seat": "Everyone on their #1 deck in the seat where their record is weakest.",
    "rehearsal": "Everyone on their #1 deck in their best seat, pods as even as possible so anyone can win.",
    "local": "Each player picks from their 1–3 decks so the tables see as much of the local meta as possible.",
    "big": "Each player brings whichever of their decks does best at big events, from their worst seat.",
    "fix": "Puts a chosen deck at one player's table so they can work on that matchup.",
    "specific": "You set a deck and/or seat for anyone; the builder fills in the rest.",
    "generic": "You pick pods, seats and decks. Just play and report, no analysis.",
    "random": "Random pods, random seats, a random deck from each player's 1–3.",
}


def pct(x, digits=1):
    return "–" if x is None else f"{x * 100:.{digits}f}%"


def pts(x):
    return f"{x * 100:+.1f} pts"


def deck_link(commander: str, url: str | None) -> str:
    name = e(short(commander))
    return f'<a href="{e(url)}" rel="noopener">{name}</a>' if url else name


def nice_date(day: str) -> str:
    dt = datetime.fromisoformat(day)
    return f"{dt:%a %b} {dt.day}"  # %-d isn't portable to Windows


def own_record(evidence: list[str]) -> str:
    """'0/7' from evidence like ['0/7 at tournament tables', ...]."""
    for ev in evidence or []:
        m = re.match(r"(\d+/\d+)", ev)
        if m:
            return m.group(1)
    return ""


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


def session_dates(games: list[dict]) -> tuple[list[str], list[str]]:
    """(upcoming soonest-first, past newest-first). Today counts as upcoming."""
    today = date.today().isoformat()
    saved = set(load("pods.json")["sessions"])
    avail = set(load("availability.json")["sessions"])
    played = {g["date"] for g in games}
    upcoming = sorted(d for d in saved | avail if d >= today)
    past = sorted((d for d in saved | played if d < today), reverse=True)
    return upcoming, past


def build_pods(day: str, cfg: dict) -> dict | None:
    """The locked-in seating for that session if it was saved, else a draft."""
    saved = load("pods.json")["sessions"].get(day)
    if saved:
        return {**saved, "draft": False}
    if day < date.today().isoformat():
        return None
    args = ["--date", day, "--json"] + (["--local"] if cfg.get("home") else [])
    try:
        _, result = podmod.plan(podmod.parser().parse_args(args))
    except SystemExit:  # e.g. fewer than 3 players saved for that day
        return None
    return {**result, "date": day, "draft": True}


def owners_by_commander(roster: dict) -> dict:
    own = defaultdict(list)
    for p in roster["players"]:
        for d in p.get("decks", []) + p.get("practice_decks", []):
            if p["name"] not in own[d["commander"]]:
                own[d["commander"]].append(p["name"])
    for d in roster.get("gauntlet_decks", []):
        own[d["commander"]].append("gauntlet")
    return own


def commander_list(roster: dict) -> list[str]:
    """Every commander for the dropdowns: roster decks plus everything seen on TopDeck, most-played first."""
    counts = defaultdict(int)
    for t in tournaments(3650):
        for s in t.get("standings", []):
            if s.get("leader"):
                counts[s["leader"]] += 1
    for p in roster["players"]:
        for d in p.get("decks", []) + p.get("practice_decks", []):
            counts[d["commander"]] += 1
    return sorted(counts, key=lambda c: (-counts[c], c))


def load_ai() -> dict | None:
    """Claude's notes from ai_notes.py, if they've been generated."""
    path = data_dir() / "ai_notes.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, json.JSONDecodeError):
        return None


# ---------- small pieces ----------

def ai_block(title: str, body: str, ai: dict) -> str:
    when = (ai.get("generated_at") or "")[:10]
    return (f'<div class="ai"><div class="ai-head"><span class="ai-badge">Claude</span><strong>{e(title)}</strong>'
            f'<span class="muted small">from these stats · {e(when)}</span></div>{body}</div>')


def badge(text: str, kind: str = "") -> str:
    return f'<span class="badge {kind}">{e(text)}</span>'


def seat_summary(s: dict) -> str:
    """One short line per seat: the seat read plus the matchup that matters most here."""
    if not s.get("seat_read") and not s.get("matchups_here"):
        return ""
    rec = s.get("seat_record") or ""
    read = {"worst seat": f"Worst seat ({rec})", "best seat": f"Best seat ({rec})",
            "no strong read": f"Seat record {rec}"}.get(s.get("seat_read"), "New seat for them")
    parts = [read]
    weak = [h for h in s.get("matchups_here", []) if h["gap"] < 0]
    if weak:
        h = weak[0]
        r = own_record(h["evidence"])
        parts.append(f"vs {short(h['commander'])}" + (f" {r}" if r else ""))
    return " · ".join(parts)


def seat_why(s: dict) -> list[str]:
    """Plain-English reasons for one seat, built from the pod builder's evidence."""
    out = []
    ev = s["evidence"]
    if not ev["tournament_games"] and not ev["practice_games"]:
        return ["No TopDeck or practice data yet. Practice reports will fill this in."]
    rec, vs = s["seat_record"], s["seat_vs_field"]
    if s["seat_read"] == "worst seat":
        out.append(f"Seat {s['seat']} is their worst seat ({rec}, {pts(vs)} vs what seat {s['seat']} usually wins).")
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
    if s.get("gauntlet"):
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
        note = f'<div class="note-line">“{e(notes[s["player"]])}”</div>' if notes.get(s["player"]) else ""
        rows.append(f'<li class="{"won" if won else ""}"><span class="sn">{i}</span><div>'
                    f'<strong>{e(s["player"])}</strong> <span class="deck">{deck_link(s["commander"], lists.get(s["player"]))}</span>'
                    f'{badge("winner", "good") if won else ""}{note}</div></li>')
    head = "Draw" if g["winner"] is None else f'{e(g["winner"])} won'
    by = f' · reported by {e(g["reported_by"])}' if g.get("reported_by") else ""
    return (f'<div class="game"><div class="game-head"><strong>{head}</strong>'
            f'<span class="muted small">{e(g["date"])}{by}</span></div>'
            f'<ol class="game-seats">{"".join(rows)}</ol></div>')


def report_form(day: str, pod: dict, results_url: str) -> str:
    """'Close the pod': winner, each seat's deck + decklist + note, group PIN."""
    if not results_url:
        return ""
    seats = "".join(
        f'<fieldset class="rf-seat" data-player="{e(s["player"])}"><legend>{s["seat"]}. {e(s["player"])}</legend>'
        f'<div class="two"><label>Deck <input name="commander" value="{e(s["commander"])}" required></label>'
        f'<label>Decklist link <input name="decklist" type="url" placeholder="https://moxfield.com/decks/…" '
        f'value="{e(s.get("decklist") or "")}"></label></div>'
        f'<label>Note / takeaway <textarea name="note" rows="2" maxlength="600" '
        f'placeholder="What did you learn? What will you work on?"></textarea></label></fieldset>'
        for s in pod["seats"])
    winners = "".join(f'<label class="chip"><input type="radio" name="winner" value="{e(s["player"])}" required> '
                      f'{e(s["player"])}</label>' for s in pod["seats"])
    winners += '<label class="chip"><input type="radio" name="winner" value="draw"> Draw</label>'
    return (f'<details class="fold"><summary>Report result</summary>'
            f'<form class="form rf" data-date="{e(day)}" data-pod="{pod["pod"]}">'
            f'<div class="field"><span class="label">Winner</span><div class="chips">{winners}</div></div>'
            f'{seats}'
            f'<div class="two"><label>Your name <input name="reporter" maxlength="60"></label>'
            f'<label>Group PIN <input name="pin" type="password" autocomplete="off" required></label></div>'
            f'<p class="muted small">Seats are already in turn order. Report each game separately.</p>'
            f'<div class="actions"><button type="submit">Submit result</button><span class="status" role="status"></span></div>'
            f'</form></details>')


# ---------- sessions ----------

def plan_form(roster: dict, results_url: str) -> str:
    """'Plan a session': date, who's playing, a scenario and its options."""
    if not results_url:
        return ""
    names = [p["name"] for p in roster["players"]]
    boxes = "".join(f'<label class="chip"><input type="checkbox" name="players" value="{e(n)}"> {e(n)}</label>'
                    for n in names)
    cards = "".join(
        f'<label class="scn"><input type="radio" name="scenario" value="{k}"{" checked" if k == "worst_seat" else ""}>'
        f'<span><strong>{e(v)}</strong><span class="muted small">{e(SCENARIO_HELP[k])}</span></span></label>'
        for k, v in podmod.SCENARIOS.items())
    pick = "".join(f'<option value="{e(n)}">{e(n)}</option>' for n in names)
    rows = "".join(
        f'<div class="as-row" data-player="{e(p["name"])}" '
        f'data-own="{e(json.dumps([d["commander"] for d in p.get("practice_decks", []) + p.get("decks", [])]))}" hidden>'
        f'<span class="as-name">{e(p["name"])}</span>'
        f'<select name="as-deck" aria-label="{e(p["name"])} deck"></select>'
        f'<select name="as-pod" aria-label="{e(p["name"])} pod" class="gen-only"><option value="">Pod</option>'
        + "".join(f'<option value="{i}">Pod {i}</option>' for i in (1, 2, 3)) + '</select>'
        f'<select name="as-seat" aria-label="{e(p["name"])} seat"><option value="">Seat: any</option>'
        + "".join(f'<option value="{i}">Seat {i}</option>' for i in (1, 2, 3, 4)) + '</select></div>'
        for p in roster["players"])
    return (f'<details class="fold plan" id="plan"><summary>Plan a session</summary>'
            f'<form class="form pf">'
            f'<div class="two"><label>Date <input name="date" type="date" required></label>'
            f'<label>Your name <input name="reporter" maxlength="60"></label></div>'
            f'<div class="field"><span class="label">Who’s playing (3 or more)</span><div class="chips">{boxes}</div></div>'
            f'<div class="field"><span class="label">Reason for the pods</span><div class="scns">{cards}</div></div>'
            f'<div class="field opt" data-for="improve fix"><label><span class="opt-improve">Player to improve</span>'
            f'<span class="opt-fix">Whose matchup</span>'
            f'<select name="player"><option value="">Choose a player</option>{pick}</select></label></div>'
            f'<div class="field opt" data-for="fix"><label>Deck they need to face <select name="target"></select></label></div>'
            f'<div class="field opt" data-for="specific generic"><span class="label">'
            f'<span class="opt-specific">Optional: lock a deck and/or seat for anyone (the rest is automatic)</span>'
            f'<span class="opt-generic">Pick a pod, seat and deck for every player</span></span>'
            f'<div class="as">{rows}</div></div>'
            f'<label>Group PIN <input name="pin" type="password" autocomplete="off" required></label>'
            f'<div class="actions"><button type="submit">Build pods</button><span class="status" role="status"></span></div>'
            f'<p class="muted small">Pods appear below within about 10 minutes. Planning the same date again replaces it.</p>'
            f'</form></details>')


def scenario_of(p: dict) -> dict:
    """The session's scenario; sessions saved before scenarios existed are described by their seat mode."""
    sc = p.get("scenario") or {}
    if not sc.get("id"):
        legacy = {"practice": "worst_seat", "best": "rehearsal", "random": "random"}.get(p.get("seat_mode"), "")
        sc = {**sc, "id": legacy, "label": podmod.SCENARIOS.get(legacy, "Custom")}
    return sc


def gaps_block(g: dict | None) -> str:
    if not g or not g.get("missing"):
        return ""
    items = "".join(
        f'<li>{badge("major", "bad") if x["major"] else ""}<strong>{e(short(x["commander"]))}</strong> '
        f'<span class="muted">{pct(x["share"])} of the field · wins {pct(x["win_rate"])} · '
        f'top cut {pct(x["top_cut_rate"], 0)}</span></li>' for x in g["missing"])
    return (f'<div class="callout"><strong>Gaps vs {e(g["lens"])}</strong>'
            f'<span class="muted small"> · top {g["top_n"]} decks nobody in the group brings</span>'
            f'<ul>{items}</ul></div>')


def pod_card(p: dict, pod: dict, games: list[dict], results_url: str, ai: dict | None) -> str:
    analysis = p.get("analysis", True)  # generic and random sessions carry no reasons
    focus = {n for n in (p.get("scenario") or {}).get("focus", [])}
    fix = {x["player"]: x["commander"] for x in (p.get("scenario") or {}).get("fix", [])}
    rows = []
    for s in pod["seats"]:
        tags = ""
        if s["player"] in focus:
            tags += badge("focus", "accent")
        if s["player"] in fix:
            tags += badge(f"fixing vs {short(fix[s['player']])}", "accent")
        if s.get("gauntlet"):
            tags += badge("gauntlet")
        if s.get("practice_rank"):
            tags += badge(f"practice #{s['practice_rank']}")
        summary = seat_summary(s) if analysis else ""
        why = ""
        if analysis and s.get("evidence"):
            live = (takeaways(games, s["player"], 1) or [s.get("takeaway")])[0]
            items = "".join(f"<li>{e(w)}</li>" for w in seat_why({**s, "takeaway": live}))
            why = f'<details class="why"><summary>Why</summary><ul>{items}</ul></details>'
        rows.append(f'<li class="seat"><span class="sn">{s["seat"]}</span><div class="seat-main">'
                    f'<div class="seat-top"><strong>{e(s["player"])}</strong>'
                    f'<span class="deck">{deck_link(s["commander"], s.get("decklist"))}</span>{tags}</div>'
                    f'{f"<div class=summary>{e(summary)}</div>" if summary else ""}{why}</div></li>')
    done = [g for g in games if g["date"] == p["date"] and g.get("pod") == pod["pod"]]
    status = badge("complete", "good") if done else ("" if p.get("draft") else badge("awaiting result"))
    brief = ""
    if ai and ai.get("session") == p["date"] and analysis:
        text = next((b["briefing"] for b in ai.get("pods", []) if b.get("pod") == pod["pod"]), "")
        brief = ai_block("Briefing", f"<p>{e(text)}</p>", ai) if text else ""
    warn = "".join(f'<p class="warn">{e(w)}</p>' for w in pod.get("warnings", []))
    results = (f'<div class="results"><h4>Results</h4>{"".join(game_card(g) for g in done)}</div>' if done else "")
    form = "" if p.get("draft") else report_form(p["date"], pod, results_url)
    return (f'<article class="card pod"><div class="pod-head"><h4>Pod {pod["pod"]}</h4>{status}</div>'
            f'{brief}<ol class="seats">{"".join(rows)}</ol>{warn}{results}{form}</article>')


def session_card(p: dict | None, day: str, games: list[dict], results_url: str, ai: dict | None,
                 past: bool = False) -> str:
    title = nice_date(day)
    if not p:
        played = [g for g in games if g["date"] == day]
        if not played:
            return ""
        return (f'<div class="session"><div class="session-head"><h3>{e(title)}</h3>{badge("logged games")}</div>'
                f'<div class="games">{"".join(game_card(g) for g in played)}</div></div>')
    sc = scenario_of(p)
    label = sc.get("label") or "Custom"
    pods = p.get("pods", [])
    done = sum(1 for pod in pods if any(g["date"] == day and g.get("pod") == pod["pod"] for g in games))
    if p.get("draft"):
        status = badge("draft", "warnb")
    elif pods and done == len(pods):
        status = badge("complete", "good")
    elif done:
        status = badge(f"{done}/{len(pods)} pods reported")
    else:
        status = badge("past, no results" if past else "upcoming", "" if past else "accent")
    by = f' · planned by {e(p["requested_by"])}' if p.get("requested_by") else ""
    extra = []
    if sc.get("focus"):
        extra.append("focus: " + ", ".join(sc["focus"]))
    if sc.get("fix"):
        extra.append("; ".join(f"{x['player']} vs {short(x['commander'])}" for x in sc["fix"]))
    if p.get("meta_label") and sc.get("id") in ("local", "big"):
        extra.append(p["meta_label"])
    help_text = SCENARIO_HELP.get(sc.get("id"), "")
    draft = ('<p class="note">Draft seating from the saved player list; it isn’t locked in until a session is planned '
             'for this date.</p>' if p.get("draft") else "")
    cards = "".join(pod_card(p, pod, games, results_url, ai) for pod in pods)
    bench = (f'<p class="muted small">Sitting out first game: {e(", ".join(p["bench"]))}. Rotate them in next.</p>'
             if p.get("bench") else "")
    stray = [g for g in games if g["date"] == day and not any(g.get("pod") == pod["pod"] for pod in pods)]
    other = (f'<div class="results"><h4>Other games that day</h4>{"".join(game_card(g) for g in stray)}</div>'
             if stray else "")
    return (f'<div class="session"><div class="session-head"><h3>{e(title)}</h3>{badge(label, "accent")}{status}</div>'
            f'<p class="muted small">{e(help_text)}{(" · " + e(" · ".join(extra))) if extra else ""}{by}</p>'
            f'{draft}{gaps_block(p.get("gaps"))}<div class="pods">{cards}</div>{bench}{other}</div>')


def render_sessions(roster, games, cfg, ai, upcoming) -> str:
    url = cfg.get("results_url", "")
    body = "".join(session_card(build_pods(d, cfg), d, games, url, ai) for d in upcoming)
    if not body:
        body = ('<p class="empty">No upcoming session yet. Use <strong>Plan a session</strong>, or ask Claude '
                '("Saturday, everyone except Jeff, worst-seat practice").</p>')
    return plan_form(roster, url) + body


# ---------- meta ----------

def render_meta(views: list[dict], roster: dict, ai: dict | None = None) -> str:
    own = owners_by_commander(roster)
    tabs, panels = [], []
    for i, v in enumerate(views):
        m, prev = v["now"], v["prev"]
        rows = sorted(m["commanders"].items(), key=lambda kv: -kv[1]["entries"])[:20]
        top_share = rows[0][1]["share"] if rows else 1
        trs = []
        gaps = []
        for n, (c, r) in enumerate(rows, 1):
            ps = prev["commanders"].get(c, {}).get("share") if prev["total_entries"] else None
            delta = "" if ps is None else (f'<span class="{"up" if r["share"] > ps else "down" if r["share"] < ps else ""}">'
                                           f'{(r["share"] - ps) * 100:+.1f}</span>')
            bar = f'<span class="bar" style="width:{r["share"] / top_share * 100:.0f}%"></span>'
            who = own.get(c, [])
            if not who and n <= 12:
                gaps.append(short(c))
            owner = (f'<span class="owner">{e(", ".join(who))}</span>' if who
                     else '<span class="owner none">nobody in the group</span>' if n <= 12 else "")
            trs.append(f'<tr><td class="num">{n}</td><td><span class="cmdr">{e(short(c))}</span>{owner}</td>'
                       f'<td class="num">{r["entries"]}</td><td class="share"><span>{pct(r["share"])}</span>{bar}</td>'
                       f'<td class="num">{delta}</td><td class="num">{pct(r["win_rate"])}</td>'
                       f'<td class="num">{pct(r["conversion"], 0)}</td></tr>')
        seats = sum(r["wins"] + r["draws"] + r["losses"] for r in m["commanders"].values())
        avg = sum(r["wins"] for r in m["commanders"].values()) / seats if seats else 0.25
        small = ('<p class="note">Small sample: read shares as who shows up, and win rates as hints.</p>'
                 if m["total_entries"] < 150 else "")
        notes = (ai or {}).get("meta", {}).get(v["id"]) or []
        analyst = (ai_block("Analyst notes", "<ul>" + "".join(f"<li>{e(n)}</li>" for n in notes) + "</ul>", ai)
                   if notes else "")
        gap_line = (f'<p class="small"><strong>Not in the group (top 12):</strong> {e(", ".join(gaps))}</p>'
                    if gaps else "")
        tabs.append(f'<button role="tab" aria-selected="{"true" if i == 0 else "false"}" data-tab="{v["id"]}">'
                    f'{e(v["label"])}</button>')
        panels.append(
            f'<div role="tabpanel" id="tab-{v["id"]}" {"" if i == 0 else "hidden"}>'
            f'<p class="muted small">{e(v["desc"])}: {m["events"]} events, {m["total_entries"]} entries. '
            f'An average seat wins {pct(avg)}; read win rates against that, not 50%.</p>{small}{analyst}{gap_line}'
            f'<div class="scroll"><table><thead><tr><th>#</th><th>Commander <span class="muted">· who brings it</span></th>'
            f'<th>Entries</th><th>Share</th><th>Δ</th><th>Win rate</th><th>Top cut</th></tr></thead>'
            f'<tbody>{"".join(trs) or "<tr><td colspan=7>No events in this window.</td></tr>"}</tbody></table></div></div>')
    return f'<div class="tabs" role="tablist">{"".join(tabs)}</div>{"".join(panels)}'


# ---------- players ----------

def practice_form(p: dict, results_url: str) -> str:
    """'Set practice decks': three dropdowns (#1-#3) plus optional decklist links."""
    if not results_url:
        return ""
    own = [d["commander"] for d in p.get("decks", [])]
    cur = p.get("practice_decks", [])
    rows = "".join(
        f'<div class="pd-row"><span class="sn">{i}</span>'
        f'<select name="deck" data-current="{e(cur[i - 1]["commander"] if i <= len(cur) else "")}" '
        f'aria-label="Practice deck {i}"></select>'
        f'<input name="list" type="url" placeholder="Decklist link (optional)" '
        f'value="{e(cur[i - 1].get("list", "") if i <= len(cur) else "")}" aria-label="Decklist link {i}"></div>'
        for i in (1, 2, 3))
    return (f'<details class="fold"><summary>Set practice decks</summary>'
            f'<form class="form pd" data-player="{e(p["name"])}" data-own="{e(json.dumps(own))}">'
            f'<p class="muted small">Up to 3 decks you want reps on, most important first. '
            f'Scenarios use these; #1 is your default.</p>{rows}'
            f'<label>Group PIN <input name="pin" type="password" autocomplete="off" required></label>'
            f'<div class="actions"><button type="submit">Save practice decks</button>'
            f'<span class="status" role="status"></span></div></form></details>')


def render_players(roster: dict, prof: Profiler, games: list[dict], ai: dict | None, results_url: str) -> str:
    coach = {c.get("name"): c for c in (ai or {}).get("players", [])}
    reports = {r["name"]: r for r in player_report([], 60)}
    cards = []
    for p in roster["players"]:
        pf = prof.profile(p)
        t = reports.get(p["name"], {}).get("tournament", {})
        link = (f'<a class="small" href="https://topdeck.gg/profile/{e(p["topdeck_id"])}" rel="noopener">'
                f'{e(p.get("topdeck_name") or "TopDeck")}</a>' if p.get("topdeck_id")
                else '<span class="muted small">no TopDeck link</span>')
        stats = (f'<div class="stats"><span><b>{t["events"]}</b> events</span>'
                 f'<span><b>{t["wins"]}-{t["losses"]}-{t["draws"]}</b> W-L-D</span>'
                 f'<span><b>{pct(t["win_rate"], 0)}</b> win rate</span><span><b>{t["top_cuts"]}</b> top cuts</span></div>'
                 if t.get("events") else '<p class="muted small">No tracked events in the last 60 days.</p>')
        practising = p.get("practice_decks") or []
        decks = (f'<div class="decks"><span class="label">Practising</span><ol>'
                 + "".join(f'<li>{deck_link(d["commander"], d.get("list"))}</li>' for d in practising) + '</ol></div>'
                 if practising else
                 f'<div class="decks"><span class="label">Decks</span> {e(", ".join(short(d["commander"]) for d in p.get("decks", [])) or "none recorded")}</div>')
        cells = []
        for s in pf["seats"]:
            cls = "best" if s["seat"] == pf["best_seat"] else "worst" if s["seat"] == pf["worst_seat"] else ""
            rec = f'{s["wins"]}/{s["games"]}' if s["games"] else "–"
            cells.append(f'<div class="cell {cls}" title="seat {s["seat"]}: {pts(s["vs_field"])} vs seat average">'
                         f'<span class="k">Seat {s["seat"]}</span><span class="v">{rec}</span></div>')

        def mlist(rows, label, cls):
            if not rows:
                return ""
            items = "".join(f'<li>{e(short(r["theirs"]))} <span class="muted">{e(own_record(r["evidence"]))}</span></li>'
                            for r in rows[:3])
            return f'<div class="{cls}"><span class="label">{label}</span><ul>{items}</ul></div>'

        mu = mlist(pf["weak"], "Needs practice vs", "weak") + mlist(pf["strong"], "Strong vs", "strong")
        mu = f'<div class="mu">{mu}</div>' if mu else '<p class="muted small">No matchup has 3+ games of evidence yet.</p>'
        tks = takeaways(games, p["name"], 2)
        take = ('<div class="takeaways"><span class="label">Takeaways</span>' + "".join(
            f'<p class="small">“{e(x["text"])}” <span class="muted">{e(x["date"])}, on {e(x["commander"])}, '
            f'{e(x["result"])}</span></p>' for x in tks) + "</div>") if tks else ""
        note = (ai_block("Coach’s note", f'<p>{e(coach[p["name"]]["note"])}</p>'
                                              f'<p class="next"><strong>Next:</strong> {e(coach[p["name"]]["next_step"])}</p>', ai)
                if p["name"] in coach else "")
        cards.append(f'<article class="card player" id="p-{e(p["name"].lower())}"><header><h3>{e(p["name"])}</h3>{link}</header>'
                     f'{stats}{decks}<div class="seatstrip">{"".join(cells)}</div>{mu}{take}{note}'
                     f'{practice_form(p, results_url)}</article>')
    return f'<div class="grid">{"".join(cards)}</div>'


# ---------- history ----------

def render_history(games, cfg, ai, past) -> str:
    url = cfg.get("results_url", "")
    if games:
        st = practice_stats(games)
        rows = sorted(st.items(), key=lambda kv: (-(kv[1]["wins"] / kv[1]["games"]), -kv[1]["games"]))
        trs = "".join(f'<tr><td>{e(n)}</td><td class="num">{s["games"]}</td><td class="num">{s["wins"]}</td>'
                      f'<td class="num">{s["draws"]}</td><td class="num">{pct(s["wins"] / s["games"], 0)}</td>'
                      f'<td>{e(", ".join(f"{short(c)} {s["wins_by_commander"][c]}/{k}" for c, k in s["by_commander"].most_common(2)))}</td></tr>'
                      for n, s in rows)
        standings = (f'<h3>Practice standings</h3><div class="scroll"><table><thead><tr><th>Player</th><th>Games</th>'
                     f'<th>Wins</th><th>Draws</th><th>Win %</th><th>Decks</th></tr></thead><tbody>{trs}</tbody></table></div>'
                     f'<p class="muted small">In 4-player games about 20–25% is average.</p>')
    else:
        standings = ('<p class="empty">No practice games logged yet. Report results on a pod, or send them to Claude in '
                     'turn order: <code>G1: Alex Tayam, Darison Sisay, Eric Kinnan, Ryan Tymna/Kraum. Winner Darison</code>.</p>')
    archive = []
    for i, d in enumerate(past[:20]):
        card = session_card(build_pods(d, cfg), d, games, url, None, past=True)
        if not card:
            continue
        n = sum(1 for g in games if g["date"] == d)
        archive.append(f'<details class="fold archive"{" open" if i == 0 else ""}><summary>{e(nice_date(d))} '
                       f'<span class="muted small">{d} · {n} game{"s" if n != 1 else ""}</span></summary>{card}</details>')
    past_html = (f'<h3>Past sessions</h3>{"".join(archive)}' if archive else
                 '<p class="muted small">Past sessions appear here with their seating, results and notes.</p>')
    return standings + past_html


# ---------- page ----------

def summary_strip(roster, games, views, upcoming, cfg) -> str:
    items = []
    if upcoming:
        p = build_pods(upcoming[0], cfg)
        n = sum(len(pod["seats"]) for pod in (p or {}).get("pods", []))
        label = scenario_of(p).get("label", "") if p and not p.get("draft") else ""
        items.append(("Next session", nice_date(upcoming[0]), f"{n} players" + (f" · {label}" if label else "")))
    local = next((v for v in views if v["id"] == "local"), None)
    if local and local["now"]["commanders"]:
        c, r = max(local["now"]["commanders"].items(), key=lambda kv: kv[1]["entries"])
        items.append(("Local #1 deck", short(c), f"{pct(r['share'])} of {local['now']['events']} events"))
    last = max((g["date"] for g in games), default="")
    items.append(("Practice games", str(len(games)), f"last {last}" if last else "none logged yet"))
    items.append(("Players", str(len(roster["players"])), "in the group"))
    return '<div class="strip">' + "".join(
        f'<div class="stat"><span class="k">{e(k)}</span><span class="v">{e(v)}</span><span class="s">{e(s)}</span></div>'
        for k, v, s in items) + "</div>"


CSS = """
:root{--bg:#f6f5f2;--surface:#fff;--sunk:#f1efea;--ink:#1c1a17;--muted:#6b665e;--line:#e3dfd6;--accent:#6b3fd1;
--accent-soft:#efe9fd;--accent-ink:#fff;--good:#1f7a4a;--good-bg:#e3f3ea;--bad:#b4372b;--bad-bg:#f9e4e1;--warn:#8a5a00;
--warn-bg:#fdf1d6;--bar:#cfc2f5;--shadow:0 1px 2px rgba(20,16,10,.05),0 2px 8px rgba(20,16,10,.04)}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#121110;--surface:#1b1a18;--sunk:#161513;
--ink:#ece8e1;--muted:#a39d93;--line:#2d2b27;--accent:#a98bf2;--accent-soft:#2a2342;--accent-ink:#121110;--good:#5fc792;
--good-bg:#16301f;--bad:#ef7d70;--bad-bg:#3a1d19;--warn:#f0c25e;--warn-bg:#3a2e12;--bar:#4a3d7a;--shadow:none}}
:root[data-theme="dark"]{--bg:#121110;--surface:#1b1a18;--sunk:#161513;--ink:#ece8e1;--muted:#a39d93;--line:#2d2b27;
--accent:#a98bf2;--accent-soft:#2a2342;--accent-ink:#121110;--good:#5fc792;--good-bg:#16301f;--bad:#ef7d70;--bad-bg:#3a1d19;
--warn:#f0c25e;--warn-bg:#3a2e12;--bar:#4a3d7a;--shadow:none}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%;scroll-behavior:smooth;scroll-padding-top:64px}
body{margin:0;background:var(--bg);color:var(--ink);font:15.5px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:var(--accent);text-underline-offset:2px}code{font-size:.85em;background:var(--sunk);padding:.1em .35em;border-radius:4px;word-break:break-word}
h2,h3,h4{letter-spacing:-.01em;line-height:1.25}
.wrap{max-width:1120px;margin:0 auto;padding:0 16px}
.top{position:sticky;top:0;z-index:10;background:color-mix(in srgb,var(--bg) 88%,transparent);backdrop-filter:blur(8px);
border-bottom:1px solid var(--line)}
.top .wrap{display:flex;align-items:center;gap:.75rem 1.25rem;flex-wrap:wrap;padding-top:10px;padding-bottom:10px}
.brand{display:flex;align-items:center;gap:.55rem;font-weight:800;font-size:1.05rem;color:var(--ink);text-decoration:none}
.logo{width:26px;height:26px;border-radius:7px;background:var(--accent);color:var(--accent-ink);display:grid;place-items:center;font-size:.85rem}
nav{display:flex;gap:.25rem;overflow-x:auto;margin-left:auto}
nav a{color:var(--muted);text-decoration:none;white-space:nowrap;font-weight:600;font-size:.92rem;padding:.35rem .7rem;border-radius:999px}
nav a:hover{color:var(--ink)}nav a.on{background:var(--accent-soft);color:var(--accent)}
.hero{padding-top:22px;padding-bottom:6px}.hero p{margin:.15rem 0 0}
.strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:14px 0 4px}
@media (max-width:520px){.strip{grid-template-columns:1fr 1fr}.stat{padding:10px 12px}.stat .v{font-size:1.05rem}}
.stat{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:12px 14px;box-shadow:var(--shadow);display:grid;gap:1px}
.stat .k{font-size:.72rem;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);font-weight:700}
.stat .v{font-size:1.25rem;font-weight:800}.stat .s{font-size:.82rem;color:var(--muted)}
section{padding:26px 0 6px}section>.wrap>h2{font-size:1.35rem;margin:0 0 .9rem;display:flex;align-items:baseline;gap:.6rem}
section>.wrap>h2 .muted{font-size:.85rem;font-weight:500}
.muted{color:var(--muted)}.small{font-size:.85rem}
.empty{padding:1rem 1.1rem;border:1px dashed var(--line);border-radius:12px;color:var(--muted);background:var(--surface)}
.note{font-size:.85rem;color:var(--muted);border-left:3px solid var(--accent);padding-left:.6rem}
.warn{font-size:.85rem;color:var(--warn);background:var(--warn-bg);padding:.35rem .6rem;border-radius:8px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:14px 16px;box-shadow:var(--shadow)}
.badge{display:inline-block;font-size:.7rem;font-weight:700;letter-spacing:.02em;padding:.12rem .5rem;border-radius:999px;
background:var(--sunk);color:var(--muted);border:1px solid var(--line);vertical-align:middle;white-space:nowrap}
.badge.accent{background:var(--accent-soft);color:var(--accent);border-color:transparent}
.badge.good{background:var(--good-bg);color:var(--good);border-color:transparent}
.badge.bad{background:var(--bad-bg);color:var(--bad);border-color:transparent;margin-right:.35rem}
.badge.warnb{background:var(--warn-bg);color:var(--warn);border-color:transparent}
.session{margin:0 0 22px}.session-head{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap}
.session-head h3{margin:0;font-size:1.15rem;margin-right:.25rem}.session>p{margin:.3rem 0 .6rem}
.pods{display:grid;gap:12px}@media (min-width:880px){.pods{grid-template-columns:1fr 1fr}}
.pod-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:.4rem}.pod-head h4{margin:0;font-size:1rem}
.seats{list-style:none;margin:0;padding:0;display:grid;gap:2px}
.seat{display:flex;gap:10px;padding:8px 0;border-top:1px solid var(--line)}.seat:first-child{border-top:0}
.sn{flex:none;width:24px;height:24px;border-radius:50%;background:var(--accent);color:var(--accent-ink);display:grid;
place-items:center;font-weight:800;font-size:.78rem;margin-top:1px}
.seat-main{min-width:0;flex:1}.seat-top{display:flex;flex-wrap:wrap;align-items:baseline;gap:.25rem .45rem}
.deck{color:var(--muted)}.summary{font-size:.86rem;color:var(--muted);margin-top:1px}
.why{margin-top:2px}.why summary{font-size:.8rem;color:var(--accent);cursor:pointer;list-style:none;display:inline}
.why summary::-webkit-details-marker{display:none}.why summary::after{content:" \\25B8"}.why[open] summary::after{content:" \\25BE"}
.why ul{margin:.3rem 0 0;padding-left:1.1rem;font-size:.86rem}.why li{margin:.1rem 0}
.callout{background:var(--surface);border:1px solid var(--line);border-left:3px solid var(--bad);border-radius:12px;padding:10px 14px;margin:.4rem 0 .8rem}
.callout ul{margin:.35rem 0 0;padding-left:1.1rem;font-size:.9rem}.callout li{margin:.15rem 0}
.ai{background:var(--accent-soft);border-radius:12px;padding:10px 12px;margin:.5rem 0}
.ai-head{display:flex;align-items:center;gap:.45rem;flex-wrap:wrap}.ai-badge{font-size:.66rem;font-weight:800;text-transform:uppercase;
letter-spacing:.06em;background:var(--accent);color:var(--accent-ink);padding:.1rem .4rem;border-radius:5px}
.ai ul{margin:.35rem 0 0;padding-left:1.1rem;font-size:.92rem}.ai li{margin:.2rem 0}.ai p{margin:.3rem 0 0;font-size:.92rem}
.results h4{margin:.8rem 0 .2rem;font-size:.75rem;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}
.game{border:1px solid var(--line);border-radius:10px;padding:8px 10px;margin:.4rem 0;background:var(--sunk)}
.game-head{display:flex;justify-content:space-between;gap:.5rem;flex-wrap:wrap;margin-bottom:.25rem}
.game-seats{list-style:none;margin:0;padding:0;display:grid;gap:5px}.game-seats li{display:flex;gap:8px;font-size:.9rem}
.game-seats li.won strong{color:var(--good)}.game-seats .sn{width:20px;height:20px;font-size:.7rem}
.note-line{color:var(--muted);font-size:.85rem}.games{display:grid;gap:8px}
.fold{margin-top:10px}.fold>summary{cursor:pointer;font-weight:700;font-size:.9rem;color:var(--accent);list-style:none;
display:inline-flex;align-items:center;gap:.35rem;padding:.35rem .75rem;border-radius:999px;background:var(--accent-soft)}
.fold>summary::-webkit-details-marker{display:none}.fold>summary::before{content:"+";font-weight:800}
.fold[open]>summary::before{content:"\\2013"}
.plan{margin:0 0 18px}.plan>summary{font-size:1rem;padding:.5rem 1rem;background:var(--accent);color:var(--accent-ink)}
.plan[open]{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:12px 14px;box-shadow:var(--shadow)}
.archive{margin:0 0 8px;border-bottom:1px solid var(--line);padding-bottom:8px}
.archive>summary{background:none;color:var(--ink);padding:.35rem 0;font-size:1rem}.archive .session{margin:.6rem 0 0}
.archive .session-head h3{display:none}
.form{display:grid;gap:12px;margin-top:12px}.form label{display:grid;gap:4px;font-size:.84rem;color:var(--muted);font-weight:600}
.form input,.form textarea,.form select{font:inherit;font-weight:400;color:var(--ink);background:var(--bg);border:1px solid var(--line);
border-radius:9px;padding:.5rem .6rem;width:100%;min-width:0}
.form input:focus,.form textarea:focus,.form select:focus{outline:2px solid var(--accent);outline-offset:1px}
.form fieldset{border:1px solid var(--line);border-radius:12px;padding:8px 12px 12px;display:grid;gap:8px;margin:0}
.form legend{font-weight:700;padding:0 .3rem;font-size:.9rem}
.two{display:grid;gap:10px;grid-template-columns:1fr 1fr}@media (max-width:520px){.two{grid-template-columns:1fr}}
.field{display:grid;gap:6px}.label{font-size:.75rem;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);font-weight:700}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{display:inline-flex!important;grid-auto-flow:column;align-items:center;gap:.35rem!important;border:1px solid var(--line);
border-radius:999px;padding:.32rem .75rem;color:var(--ink)!important;font-size:.9rem!important;cursor:pointer;background:var(--bg)}
.chip input{width:auto!important;margin:0}.chip:has(input:checked){background:var(--accent-soft);border-color:var(--accent);color:var(--accent)!important}
.scns{display:grid;gap:8px;grid-template-columns:repeat(auto-fill,minmax(230px,1fr))}
.scn{display:flex!important;gap:.55rem!important;align-items:flex-start;border:1px solid var(--line);border-radius:12px;padding:.6rem .7rem;
cursor:pointer;background:var(--bg);color:var(--ink)!important}
.scn input{width:auto!important;margin-top:.2rem}.scn>span{display:grid;gap:2px;font-weight:400}
.scn:has(input:checked){border-color:var(--accent);background:var(--accent-soft)}
.opt[hidden],.as-row[hidden]{display:none}
.as{display:grid;gap:6px}.as-row{display:grid;grid-template-columns:90px 1fr auto auto;gap:6px;align-items:center}
.as-name{font-weight:700;font-size:.9rem}@media (max-width:560px){.as-row{grid-template-columns:1fr 1fr}.as-name{grid-column:1/-1}}
.actions{display:flex;align-items:center;gap:.75rem;flex-wrap:wrap}
.form button{font:inherit;font-weight:700;background:var(--accent);color:var(--accent-ink);border:0;border-radius:999px;
padding:.55rem 1.2rem;cursor:pointer}.form button[disabled]{opacity:.6;cursor:default}
.status{font-size:.9rem}.status.ok{color:var(--good)}.status.err{color:var(--bad)}
.pd-row{display:grid;grid-template-columns:auto 1fr 1fr;gap:6px 8px;align-items:center}
@media (max-width:520px){.pd-row{grid-template-columns:auto 1fr}.pd-row input{grid-column:2}}
.pd-row .sn{background:var(--bar);color:var(--ink)}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}
.tabs button{font:inherit;font-weight:700;font-size:.9rem;border:1px solid var(--line);background:var(--surface);color:var(--ink);
padding:.35rem .9rem;border-radius:999px;cursor:pointer}
.tabs button[aria-selected="true"]{background:var(--accent);color:var(--accent-ink);border-color:var(--accent)}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;border:1px solid var(--line);border-radius:14px;background:var(--surface);box-shadow:var(--shadow)}
table{border-collapse:collapse;width:100%;font-size:.9rem}
th,td{padding:.5rem .65rem;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{font-size:.72rem;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);white-space:nowrap;background:var(--sunk)}
tr:last-child td{border-bottom:0}td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.cmdr{font-weight:650}.owner{display:block;font-size:.76rem;color:var(--accent)}.owner.none{color:var(--bad)}
td.share{min-width:100px;font-variant-numeric:tabular-nums}
td.share .bar{display:block;height:5px;margin-top:4px;background:var(--bar);border-radius:3px}
.up{color:var(--good)}.down{color:var(--bad)}
.grid{display:grid;gap:14px}@media (min-width:720px){.grid{grid-template-columns:1fr 1fr}}
@media (min-width:1040px){.grid{grid-template-columns:1fr 1fr 1fr}}
.player{display:flex;flex-direction:column;gap:8px}
.player header{display:flex;justify-content:space-between;align-items:baseline;gap:.5rem;flex-wrap:wrap}.player h3{margin:0;font-size:1.1rem}
.stats{display:flex;flex-wrap:wrap;gap:.25rem .9rem;font-size:.82rem;color:var(--muted)}.stats b{color:var(--ink)}
.decks{font-size:.9rem}.decks ol{margin:.15rem 0 0;padding-left:1.2rem}
.seatstrip{display:grid;grid-template-columns:repeat(4,1fr);gap:6px}
.cell{border:1px solid var(--line);border-radius:9px;padding:.3rem .4rem;text-align:center;background:var(--sunk)}
.cell .k{display:block;font-size:.66rem;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.cell .v{font-weight:800;font-variant-numeric:tabular-nums}
.cell.best{background:var(--good-bg);border-color:var(--good)}.cell.worst{background:var(--bad-bg);border-color:var(--bad)}
.mu{display:grid;grid-template-columns:1fr 1fr;gap:8px}.mu ul{margin:.15rem 0 0;padding-left:1.1rem;font-size:.87rem}
.mu .weak .label{color:var(--bad)}.mu .strong .label{color:var(--good)}
.takeaways p{margin:.15rem 0}
h3{font-size:1.05rem;margin:1.2rem 0 .5rem}
footer{margin-top:30px;border-top:1px solid var(--line);padding:18px 0 44px;font-size:.84rem;color:var(--muted)}
"""

JS = r"""
// tabs
document.querySelectorAll('.tabs').forEach(function(bar){
  bar.addEventListener('click',function(ev){
    var b=ev.target.closest('button[data-tab]'); if(!b) return;
    bar.querySelectorAll('button').forEach(function(x){x.setAttribute('aria-selected',x===b?'true':'false')});
    bar.parentElement.querySelectorAll('[role=tabpanel]').forEach(function(p){p.hidden=p.id!=='tab-'+b.dataset.tab});
  });
});
// highlight the nav link for the section in view
(function(){
  var links={}; document.querySelectorAll('nav a').forEach(function(a){links[a.getAttribute('href').slice(1)]=a;});
  if(!('IntersectionObserver' in window)) return;
  var io=new IntersectionObserver(function(es){es.forEach(function(en){
    if(en.isIntersecting){Object.values(links).forEach(function(a){a.classList.remove('on')}); var a=links[en.target.id]; if(a)a.classList.add('on');}
  })},{rootMargin:'-40% 0px -55% 0px'});
  document.querySelectorAll('section[id]').forEach(function(s){io.observe(s)});
})();

var RESULTS_URL = document.body.dataset.resultsUrl;
var CMDRS = []; try { CMDRS = JSON.parse(document.getElementById('cmdrs').textContent); } catch (e) {}
function store(k, v){ try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } }
function shortName(c){ return c.split(' / ').map(function(p){ return p.split(',')[0].split(' the ')[0].trim(); }).join('/'); }
function opt(v, t){ var o = document.createElement('option'); o.value = v; o.textContent = t; return o; }
function fillDecks(sel, own, blank){
  sel.innerHTML = ''; sel.appendChild(opt('', blank));
  var mine = document.createElement('optgroup'); mine.label = 'Their decks';
  own.forEach(function(c){ mine.appendChild(opt(c, shortName(c))); }); if (own.length) sel.appendChild(mine);
  var all = document.createElement('optgroup'); all.label = 'All commanders (most played first)';
  CMDRS.forEach(function(c){ if (own.indexOf(c) < 0) all.appendChild(opt(c, shortName(c) + (shortName(c) !== c ? ' — ' + c : ''))); });
  sel.appendChild(all);
}
function send(f, body, okText, after){
  var status = f.querySelector('.status'), btn = f.querySelector('button[type=submit]');
  btn.disabled = true; status.className = 'status'; status.textContent = 'Sending…';
  fetch(RESULTS_URL, { method: 'POST', body: JSON.stringify(body) })
    .then(function(r){ return r.json(); })
    .then(function(res){
      if (!res.ok) throw new Error(res.error || 'Not saved.');
      store('cedh-pin', body.pin); if (body.reporter !== undefined) store('cedh-reporter', body.reporter);
      status.className = 'status ok'; status.textContent = okText; if (after) after();
    })
    .catch(function(err){ status.className = 'status err'; status.textContent = 'Not saved: ' + err.message; })
    .then(function(){ btn.disabled = false; });
}
function bad(f, msg){ var s = f.querySelector('.status'); s.className = 'status err'; s.textContent = msg; }
function remember(f){
  var pin = f.querySelector('[name=pin]'), who = f.querySelector('[name=reporter]');
  if (pin) pin.value = store('cedh-pin') || ''; if (who) who.value = store('cedh-reporter') || '';
}

// Report result
document.querySelectorAll('form.rf').forEach(function(f){
  remember(f);
  f.addEventListener('submit', function(ev){
    ev.preventDefault();
    var winner = f.querySelector('[name=winner]:checked');
    if (!winner) return bad(f, 'Pick the winner (or Draw).');
    var seats = Array.prototype.map.call(f.querySelectorAll('.rf-seat'), function(fs, i){
      return { seat: i + 1, player: fs.dataset.player, commander: fs.querySelector('[name=commander]').value.trim(),
               decklist: fs.querySelector('[name=decklist]').value.trim(), note: fs.querySelector('[name=note]').value.trim() };
    });
    send(f, { type: 'result', date: f.dataset.date, pod: Number(f.dataset.pod), winner: winner.value, seats: seats,
              reporter: f.querySelector('[name=reporter]').value.trim(), pin: f.querySelector('[name=pin]').value.trim() },
         'Saved. It shows here within about 10 minutes. Report the next game the same way.',
         function(){ f.querySelectorAll('[name=note]').forEach(function(t){ t.value = ''; });
                     f.querySelectorAll('[name=winner]').forEach(function(r){ r.checked = false; }); });
  });
});

// Plan a session
document.querySelectorAll('form.pf').forEach(function(f){
  remember(f);
  var target = f.querySelector('[name=target]'); fillDecks(target, [], 'Choose a deck');
  f.querySelectorAll('.as-row').forEach(function(row){
    var own = []; try { own = JSON.parse(row.dataset.own || '[]'); } catch (e) {}
    own = own.filter(function(c, i){ return own.indexOf(c) === i; });
    fillDecks(row.querySelector('[name=as-deck]'), own, 'Deck: auto');
  });
  function scenario(){ var r = f.querySelector('[name=scenario]:checked'); return r ? r.value : ''; }
  function sync(){
    var s = scenario(), chosen = {};
    f.querySelectorAll('[name=players]:checked').forEach(function(x){ chosen[x.value] = 1; });
    f.querySelectorAll('.opt').forEach(function(o){ o.hidden = o.dataset.for.split(' ').indexOf(s) < 0; });
    f.querySelectorAll('.opt-improve').forEach(function(x){ x.hidden = s !== 'improve'; });
    f.querySelectorAll('.opt-fix').forEach(function(x){ x.hidden = s !== 'fix'; });
    f.querySelectorAll('.opt-specific').forEach(function(x){ x.hidden = s !== 'specific'; });
    f.querySelectorAll('.opt-generic').forEach(function(x){ x.hidden = s !== 'generic'; });
    f.querySelectorAll('.gen-only').forEach(function(x){ x.hidden = s !== 'generic'; });
    f.querySelectorAll('.as-row').forEach(function(r){ r.hidden = !chosen[r.dataset.player]; });
  }
  f.addEventListener('change', sync); sync();
  f.addEventListener('submit', function(ev){
    ev.preventDefault();
    var s = scenario(), players = Array.prototype.map.call(f.querySelectorAll('[name=players]:checked'), function(x){ return x.value; });
    if (players.length < 3) return bad(f, 'Pick at least 3 players.');
    var body = { type: 'session', scenario: s, date: f.querySelector('[name=date]').value, players: players,
                 reporter: f.querySelector('[name=reporter]').value.trim(), pin: f.querySelector('[name=pin]').value.trim() };
    if (s === 'improve' || s === 'fix') {
      body.player = f.querySelector('[name=player]').value;
      if (!body.player) return bad(f, 'Choose the player.');
      if (players.indexOf(body.player) < 0) return bad(f, body.player + ' isn’t ticked as playing.');
    }
    if (s === 'fix') { body.target = target.value; if (!body.target) return bad(f, 'Choose the deck they need to face.'); }
    if (s === 'specific' || s === 'generic') {
      body.assign = [];
      f.querySelectorAll('.as-row').forEach(function(r){
        if (r.hidden) return;
        body.assign.push({ player: r.dataset.player, commander: r.querySelector('[name=as-deck]').value,
                           pod: r.querySelector('[name=as-pod]').value, seat: r.querySelector('[name=as-seat]').value });
      });
      if (s === 'generic' && body.assign.some(function(a){ return !a.commander || !a.pod || !a.seat; }))
        return bad(f, 'Generic practice: pick a pod, seat and deck for every player.');
    }
    send(f, body, 'Request saved. Pods for ' + body.date + ' appear here within about 10 minutes.');
  });
});

// Set practice decks
document.querySelectorAll('form.pd').forEach(function(f){
  remember(f);
  var own = []; try { own = JSON.parse(f.dataset.own || '[]'); } catch (e) {}
  f.querySelectorAll('select[name=deck]').forEach(function(sel){ fillDecks(sel, own, '— none —'); sel.value = sel.dataset.current || ''; });
  f.addEventListener('submit', function(ev){
    ev.preventDefault();
    var decks = [], seen = {};
    f.querySelectorAll('.pd-row').forEach(function(row){
      var c = row.querySelector('select').value, l = row.querySelector('input').value.trim();
      if (c && !seen[c]) { seen[c] = 1; decks.push({ commander: c, list: l }); }
    });
    send(f, { type: 'practice', player: f.dataset.player, decks: decks, pin: f.querySelector('[name=pin]').value.trim() },
         decks.length ? 'Saved. Your profile and next pods use these within about 10 minutes.' : 'Saved: practice decks cleared.');
  });
});
"""


def page(cfg: dict) -> str:
    roster = load("roster.json")
    games = load("games.json")["games"]
    prof = Profiler(30, 16)
    ai = load_ai()
    views = meta_views(cfg)
    upcoming, past = session_dates(games)
    url = cfg.get("results_url", "")
    sections = [
        ("sessions", "Sessions", "plan, play, report", render_sessions(roster, games, cfg, ai, upcoming)),
        ("meta", "Meta", "what we’ll face", render_meta(views, roster, ai)),
        ("players", "Players", "profiles and practice decks", render_players(roster, prof, games, ai, url)),
        ("history", "History", "standings and past sessions", render_history(games, cfg, ai, past)),
    ]
    nav = "".join(f'<a href="#{sid}">{e(t)}</a>' for sid, t, _, _ in sections)
    body = "".join(f'<section id="{sid}"><div class="wrap"><h2>{e(t)} <span class="muted">{e(sub)}</span></h2>{h}</div></section>'
                   for sid, t, sub, h in sections)
    home = cfg.get("home")
    sub = f"Practice pods and meta for our cEDH group · home: {area_label(home)}" if home else "Practice pods and meta for our cEDH group"
    stamp = datetime.now(timezone.utc).strftime("%b %d, %Y %H:%M UTC")
    cmdrs = json.dumps(commander_list(roster), ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>cEDH Playgroup</title><meta name="description" content="Practice pods, local and big-event meta, and player profiles for our cEDH playgroup.">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%236b3fd1'/%3E%3Ctext x='16' y='22' font-size='16' text-anchor='middle' fill='white' font-family='sans-serif' font-weight='700'%3EC%3C/text%3E%3C/svg%3E">
<style>{CSS}</style></head><body data-results-url="{e(url)}">
<header class="top"><div class="wrap"><a class="brand" href="#"><span class="logo">C</span>cEDH Playgroup</a><nav>{nav}</nav></div></header>
<div class="wrap hero"><p class="muted small">{e(sub)}</p>{summary_strip(roster, games, views, upcoming, cfg)}</div>
<main>{body}</main>
<footer><div class="wrap"><p>Tournament data from <a href="https://topdeck.gg">TopDeck.gg</a>. Updated {e(stamp)}.</p>
<p>Win rates are four-player numbers: an average seat wins about 20% once draws are counted. Samples under ~10 games are hints, not findings.
Built by the <a href="{REPO_URL}">cedh-playgroup</a> skill.</p></div></footer>
<script id="cmdrs" type="application/json">{cmdrs}</script>
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
