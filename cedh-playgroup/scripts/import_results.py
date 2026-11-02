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
        pl = find_player(roster, str(s.get("player", "")))
        if not pl:
            return None, f"unknown player {s.get('player')!r}"
        mine = [d["commander"] for d in pl.get("decks", []) + pl.get("practice_decks", [])]
        cmdr = str(s.get("commander") or "").strip()
        if not cmdr:
            return None, f"no deck for {pl['name']}"
        seats.append({"player": pl["name"], "commander": match_commander(cmdr, mine)[0]})
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
    added, skipped = 0, []
    for sub in sorted(subs, key=lambda s: s.get("received", "")):
        if sub.get("id") in seen:
            continue
        game, problem = to_game(sub, roster)
        if not game:
            skipped.append(f"{sub.get('id')}: {problem}")
            continue
        games["games"].append(game)
        seen.add(game["source"])
        added += 1
        # remember decklists on the roster when a deck didn't have one yet
        for name, url in game.get("decklists", {}).items():
            pl = find_player(roster, name)
            cmdr = next(s["commander"] for s in game["seats"] if s["player"] == name)
            for d in pl.get("decks", []):
                if d["commander"] == cmdr and not d.get("list"):
                    d["list"] = url
        print(f"Imported: {game['date']} pod {game.get('pod', '-')}: "
              + ", ".join(f"{i}. {s['player']}" for i, s in enumerate(game["seats"], 1))
              + f" -> {'draw' if game['winner'] is None else game['winner'] + ' won'}")
    if added:
        save("games.json", games)
        save("roster.json", roster)
    for s in skipped:
        print(f"Skipped {s}", file=sys.stderr)
    print(f"{added} new result(s) imported, {len(skipped)} skipped.")


if __name__ == "__main__":
    main()
