"""Import pod results submitted on the website.

The site's "Report result" form posts to a Google Apps Script web app
(apps-script/Code.gs), which keeps submissions in a Google Sheet. This reads
them from that web app and records each new one as a practice game: seats in
turn order, winner, each player's note (their takeaway from the pod) and the
decklist they used. Already-imported submissions are skipped by id.

  python import_results.py                 # uses results_url from config.json
  python import_results.py --file dump.json  # offline / testing
"""
import argparse
import json
import sys
import urllib.request

from common import die, find_player, load, match_commander, save

MAX_NOTE = 600


def fetch(url: str) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "cedh-playgroup-skill"})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = json.load(r)
    if not body.get("ok"):
        die(f"results endpoint said: {body.get('error')}")
    return body["results"]


def to_game(sub: dict, roster: dict) -> tuple[dict | None, str]:
    """Validate one submission; returns (game, problem)."""
    p = sub.get("payload") or {}
    seats_in = p.get("seats") or []
    if not 3 <= len(seats_in) <= 5:
        return None, "needs 3 to 5 seats"
    seats, notes, lists = [], {}, {}
    for s in seats_in:
        name = " ".join(str(s.get("player") or "").split())[:40]
        pl = find_player(roster, name) if name else None
        if not pl and not (s.get("guest") and name):
            return None, f"unknown player {s.get('player')!r}"
        pl = pl or {"name": name, "guest": True}  # a random / guest seated in a manual pod
        mine = [d["commander"] for d in pl.get("decks", []) + pl.get("practice_decks", [])]
        cmdr = str(s.get("commander") or "").strip()
        if not cmdr:
            return None, f"no deck for {pl['name']}"
        seats.append({"player": pl["name"], "commander": match_commander(cmdr, mine)[0],
                      **({"guest": True} if pl.get("guest") else {})})
        note = " ".join(str(s.get("note") or "").split())[:MAX_NOTE]
        if note:
            notes[pl["name"]] = note
        url = str(s.get("decklist") or "").strip()
        if url.startswith(("https://", "http://")):
            lists[pl["name"]] = url
    names = [s["player"] for s in seats]
    if len(set(names)) != len(names):
        return None, "a player is seated twice"
    w = str(p.get("winner") or "").strip()
    if w.lower() == "draw":
        winner = None
    else:
        match = next((n for n in names if n.lower() == w.lower()), None)
        if not match:
            return None, f"winner {w!r} isn't seated"
        winner = match
    date = str(p.get("date") or sub.get("received", ""))[:10]
    game = {"date": date, "seats": seats, "winner": winner, "turn_order": True, "note": "",
            "source": sub["id"], "reported_by": str(p.get("reporter") or "")[:60]}
    if p.get("pod"):
        game["pod"] = int(p["pod"])
    if p.get("game"):
        game["game"] = int(p["game"])
    if notes:
        game["notes"] = notes
    if lists:
        game["decklists"] = lists
    return game, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="JSON from the endpoint, instead of fetching")
    ap.add_argument("--ignore", nargs="+", metavar="ID", help="never import these submission ids (tests, mistakes)")
    a = ap.parse_args()
    if a.file:
        with open(a.file, encoding="utf-8") as f:
            subs = json.load(f).get("results", [])
    else:
        url = load("config.json").get("results_url")
        if not url:
            print("No results_url in config.json yet; nothing to import.")
            return
        subs = fetch(url)

    roster = load("roster.json")
    games = load("games.json")
    seen = {g.get("source") for g in games["games"] if g.get("source")}
    # test or mistaken submissions: `import_results.py --ignore ID` (kept in config.json)
    cfg = load("config.json")
    if a.ignore:
        cfg["ignore_submissions"] = sorted(set(cfg.get("ignore_submissions", [])) | set(a.ignore))
        save("config.json", cfg)
        print(f"Ignoring {len(a.ignore)} submission(s) from now on.")
    seen |= set(cfg.get("ignore_submissions", []))
    sessions = load("pods.json")["sessions"]
    added, skipped = 0, []
    for sub in sorted(subs, key=lambda s: s.get("received", "")):
        if sub.get("id") in seen:
            continue
        if (sub.get("payload") or {}).get("type", "result") != "result":
            continue  # "Plan a session" (plan_sessions.py) and "practice" (apply_practice) requests
        game, problem = to_game(sub, roster)
        if not game:
            skipped.append(f"{sub.get('id')}: {problem}")
            continue
        sess = sessions.get(game["date"])
        if "game" not in game and sess and sess.get("games", 1) > 1:
            game["game"] = sess.get("game", 1)  # a multi-game session: the game being played now
        games["games"].append(game)
        seen.add(game["source"])
        added += 1
        # remember decklists on the roster when a deck didn't have one yet
        for name, url in game.get("decklists", {}).items():
            pl = find_player(roster, name)
            if not pl:
                continue  # guests have no roster decks
            cmdr = next(s["commander"] for s in game["seats"] if s["player"] == name)
            for d in pl.get("decks", []):
                if d["commander"] == cmdr and not d.get("list"):
                    d["list"] = url
        print(f"Imported: {game['date']}{' game ' + str(game['game']) if game.get('game') else ''} pod {game.get('pod', '-')}: "
              + ", ".join(f"{i}. {s['player']}" for i, s in enumerate(game["seats"], 1))
              + f" -> {'draw' if game['winner'] is None else game['winner'] + ' won'}")
    changed = apply_practice(subs, roster, seen)
    if added:
        save("games.json", games)
    if added or changed:
        save("roster.json", roster)
    for s in skipped:
        print(f"Skipped {s}", file=sys.stderr)
    print(f"{added} new result(s) imported, {len(skipped)} skipped, {changed} practice-deck update(s).")


def apply_practice(subs: list[dict], roster: dict, ignored: set) -> int:
    """'Set practice decks' requests from the players tab: up to 3 decks in
    priority order, each with an optional decklist link. Latest per player wins."""
    done = set(roster.get("applied_requests", []))
    reqs = [s for s in subs if (s.get("payload") or {}).get("type") == "practice"
            and s.get("id") not in done and s.get("id") not in ignored]
    changed = 0
    for sub in sorted(reqs, key=lambda s: s.get("received", "")):
        roster.setdefault("applied_requests", []).append(sub["id"])
        p = sub["payload"]
        pl = find_player(roster, str(p.get("player", "")))
        if not pl:
            print(f"Skipped practice decks {sub['id']}: unknown player {p.get('player')!r}", file=sys.stderr)
            continue
        out = []
        for d in (p.get("decks") or [])[:3]:
            mine = [x["commander"] for x in pl.get("decks", [])]
            name = str(d.get("commander") or "").strip()
            if not name:
                continue
            cmdr = match_commander(name, mine)[0]
            if any(o["commander"] == cmdr for o in out):
                continue
            url = str(d.get("list") or "").strip()
            out.append({"commander": cmdr, **({"list": url} if url.startswith(("https://", "http://")) else {})})
        pl["practice_decks"] = out  # an empty list clears them
        for o in out:
            deck = next((x for x in pl.setdefault("decks", []) if x["commander"] == o["commander"]), None)
            if not deck:
                deck = {"commander": o["commander"], "primary": not pl["decks"]}
                pl["decks"].append(deck)
            if o.get("list"):
                deck["list"] = o["list"]
        changed += 1
        print(f"Practice decks for {pl['name']}: " + (", ".join(f"{i}. {o['commander']}" for i, o in
                                                              enumerate(out, 1)) or "cleared"))
    return changed


if __name__ == "__main__":
    main()
