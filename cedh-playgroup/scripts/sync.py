"""Pull completed EDH tournaments from TopDeck.gg into the local cache.

TopDeck has no "tournaments for player X" endpoint, so one sync of recent
events feeds both the meta report and every playgroup member's stats.

Usage:
  python sync.py                 # last 30 days
  python sync.py --days 90       # longer history for player stats
  python sync.py --import f.json # load a saved /v2/tournaments response instead
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

from common import ATTRIBUTION, die, fmt_date, load, now_unix, save, secret

API = "https://topdeck.gg/api/v2/tournaments"
WINDOW = 7 * 86400  # the bulk endpoint is heavy; ask for a week at a time
COLUMNS = ["name", "id", "wins", "draws", "losses", "winRate"]


def post(body: dict, key: str) -> list:
    req = urllib.request.Request(
        API,
        data=json.dumps(body).encode(),
        headers={"Authorization": key, "Content-Type": "application/json",
                 "User-Agent": "cedh-playgroup-skill"},
        method="POST",
    )
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = int(e.headers.get("Retry-After") or 30 * (attempt + 1))
                print(f"  rate limited, waiting {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            if e.code == 401:
                die("TopDeck rejected the API key (401). Check TOPDECK_API_KEY.")
            if e.code >= 500 and attempt < 5:
                time.sleep(5 * (attempt + 1))
                continue
            die(f"TopDeck returned HTTP {e.code}: {e.read()[:300]!r}")
        except urllib.error.URLError as e:
            if attempt < 5:
                time.sleep(5 * (attempt + 1))
                continue
            die(f"could not reach TopDeck: {e}")
    die("gave up after repeated rate limiting")


def slim(t: dict) -> dict:
    """Keep only what the reports use, so the cache stays small."""
    keep = {k: t.get(k) for k in ("TID", "tournamentName", "startDate", "swissNum", "topCut", "format")}
    keep["eventData"] = {k: (t.get("eventData") or {}).get(k) for k in ("city", "state", "location")}
    keep["standings"] = [
        {k: s.get(k) for k in ("standing", "name", "id", "leader", "wins", "draws", "losses", "winRate")}
        for s in t.get("standings", [])
    ]
    for i, s in enumerate(keep["standings"], 1):
        s["standing"] = s["standing"] or i
    keep["rounds"] = [
        {"round": r.get("round"),
         "tables": [{"players": [p.get("id") for p in tb.get("players", [])],
                     "winner_id": tb.get("winner_id"), "status": tb.get("status")}
                    for tb in r.get("tables", [])]}
        for r in t.get("rounds", []) or []
    ]
    keep["participants"] = len(keep["standings"])
    return keep


def merge(tournaments: list) -> int:
    cache = load("cache/tournaments.json")
    n = 0
    for t in tournaments:
        if t.get("format") not in (None, "EDH") or t.get("isTeamEvent"):
            continue
        cache["tournaments"][t["TID"]] = slim(t)
        n += 1
    cache["synced"].append({"at": now_unix(), "count": n})
    cache["synced"] = cache["synced"][-50:]
    save("cache/tournaments.json", cache)
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--import", dest="imp", help="JSON file holding a /v2/tournaments response")
    a = ap.parse_args()

    if a.imp:
        with open(a.imp, encoding="utf-8") as f:
            data = json.load(f)
        n = merge(data if isinstance(data, list) else data.get("tournaments", data))
        print(f"Imported {n} tournaments from {a.imp}.")
        print(ATTRIBUTION)
        return

    key = secret("TOPDECK_API_KEY")
    if not key:
        die("TOPDECK_API_KEY is not set. Get a free key at https://topdeck.gg/account "
            "and set it as an environment variable.")

    end = now_unix()
    start = end - a.days * 86400
    total = 0
    while start < end:
        stop = min(start + WINDOW, end)
        body = {"game": "Magic: The Gathering", "format": "EDH",
                "start": start, "end": stop, "columns": COLUMNS, "rounds": True}
        batch = post(body, key)
        got = merge(batch)
        total += got
        print(f"  {fmt_date(start)} .. {fmt_date(stop)}: {got} tournaments")
        start = stop
        time.sleep(1)
    cache = load("cache/tournaments.json")
    print(f"Synced {total} tournaments ({len(cache['tournaments'])} cached in total).")
    print(ATTRIBUTION)


if __name__ == "__main__":
    main()
