"""Reports built from the TopDeck cache plus the group's own logs.

  meta     [--days 30] [--min-players 16] [--top 25] [--json]
  meta     --local | --zip 18201 [--miles 100]   # only events near you (all sizes unless --min-players)
  meta     --big                                 # only big events (over 100 players), anywhere
  matchups CMDR [--days 30] [--min-players 16] [--local | --zip Z --miles M] [--json]
  matchups [CMDR] --player NAME [--days 90]    # that player's own tables, all event sizes
  players  [NAME ...] [--days 30] [--json]

Four-player cEDH tables: a deck winning 1 game in 4 is average, so win
rates are read against 25%, not 50%.
"""
import argparse
import json
from collections import Counter, defaultdict

from common import (ATTRIBUTION, die, find_player, fmt_date, load, match_commander, miles_between,
                    now_unix, zip_location)

BASELINE = 0.25


def tournaments(days: int, min_players: int = 0, offset_days: int = 0, area: dict | None = None) -> list[dict]:
    """Events in the window. With `area` ({lat, lng, miles}), only events whose
    TopDeck coordinates fall inside that radius; events without coordinates
    (online, or synced before coordinates were kept) are left out."""
    cache = load("cache/tournaments.json")
    hi = now_unix() - offset_days * 86400
    lo = hi - days * 86400
    out = []
    for t in cache["tournaments"].values():
        if not (lo <= (t.get("startDate") or 0) < hi and t.get("participants", 0) >= min_players):
            continue
        if area:
            ev = t.get("eventData") or {}
            if ev.get("lat") is None or ev.get("lng") is None:
                continue
            if miles_between(area["lat"], area["lng"], ev["lat"], ev["lng"]) > area["miles"]:
                continue
        out.append(t)
    return out


def resolve_area(a) -> dict | None:
    """--zip Z [--miles M] for a one-off area, --local for the saved home area."""
    if getattr(a, "zip", None):
        loc = zip_location(a.zip)
        return {**loc, "miles": a.miles or 50}
    if getattr(a, "local", False):
        home = load("config.json").get("home")
        if not home:
            die("no home area saved yet: python playgroup.py home --zip 92101 --miles 50")
        return {**home, "miles": a.miles or home["miles"]}
    return None


def big_min() -> int:
    """Player count that makes an event 'big' for --big (saved with playgroup.py home --big-min)."""
    return int(load("config.json").get("big_min_players", 101))


def area_label(area: dict | None) -> str:
    return f"within {area['miles']:g} mi of {area['zip']} ({area['place']})" if area else "all TopDeck events"


def coverage(days: int, offset_days: int = 0) -> tuple[int, int]:
    """(events in window, events with coordinates): how much a radius filter can see."""
    ts = tournaments(days, 0, offset_days)
    return len(ts), sum(1 for t in ts if (t.get("eventData") or {}).get("lat") is not None)


def meta(ts: list[dict]) -> dict:
    rows = defaultdict(lambda: {"entries": 0, "wins": 0, "draws": 0, "losses": 0,
                                "cut_entries": 0, "top_cuts": 0, "best": None})
    total = 0
    for t in ts:
        cut = t.get("topCut") or 0
        for s in t.get("standings", []):
            c = s.get("leader")
            if not c:
                continue
            total += 1
            r = rows[c]
            r["entries"] += 1
            for k in ("wins", "draws", "losses"):
                r[k] += s.get(k) or 0
            if cut:
                r["cut_entries"] += 1
                if s["standing"] <= cut:
                    r["top_cuts"] += 1
            if r["best"] is None or s["standing"] < r["best"]:
                r["best"] = s["standing"]
    out = {}
    for c, r in rows.items():
        g = r["wins"] + r["draws"] + r["losses"]
        out[c] = {**r, "share": r["entries"] / total if total else 0,
                  "win_rate": r["wins"] / g if g else None,
                  "draw_rate": r["draws"] / g if g else None,
                  "conversion": r["top_cuts"] / r["cut_entries"] if r["cut_entries"] else None}
    return {"total_entries": total, "events": len(ts), "commanders": out}


def matchup_table(ts: list[dict]) -> dict:
    """pair[(A, B)] = games A and B shared a table, and who won them."""
    pair = defaultdict(lambda: {"games": 0, "a_wins": 0, "b_wins": 0, "other": 0})
    for t in ts:
        leader = {s["id"]: s.get("leader") for s in t.get("standings", []) if s.get("id")}
        for r in t.get("rounds", []):
            for tb in r.get("tables", []):
                ids = [i for i in tb.get("players", []) if i]
                if len(ids) < 3:
                    continue
                cs = [leader.get(i) for i in ids]
                for i, a in enumerate(cs):
                    for j, b in enumerate(cs):
                        if i == j or not a or not b:
                            continue
                        p = pair[(a, b)]
                        p["games"] += 1
                        if tb.get("winner_id") == ids[i]:
                            p["a_wins"] += 1
                        elif tb.get("winner_id") == ids[j]:
                            p["b_wins"] += 1
                        else:
                            p["other"] += 1
    return pair


def player_matchups(ts: list[dict], pid: str) -> dict:
    """One player's own tournament tables: mine[(my_commander, their_commander)].

    The field-wide table says how a deck does; this says how *this pilot* does
    on it, which is what "work on my bad matchups" means."""
    mine = defaultdict(lambda: {"games": 0, "a_wins": 0, "b_wins": 0, "other": 0})
    if not pid:
        return mine
    for t in ts:
        leader = {s["id"]: s.get("leader") for s in t.get("standings", []) if s.get("id")}
        me = leader.get(pid)
        if not me:
            continue
        for r in t.get("rounds", []):
            for tb in r.get("tables", []):
                ids = [i for i in tb.get("players", []) if i]
                if pid not in ids or len(ids) < 3:
                    continue
                w = tb.get("winner_id")
                for o in ids:
                    theirs = leader.get(o)
                    if o == pid or not theirs:
                        continue
                    p = mine[(me, theirs)]
                    p["games"] += 1
                    if w == pid:
                        p["a_wins"] += 1
                    elif w == o:
                        p["b_wins"] += 1
                    else:
                        p["other"] += 1
    return mine


def seat_field(ts: list[dict]) -> list[float]:
    """Field win rate by turn order at 4-player tables. TopDeck lists table
    players in seat order: seat 1 wins ~23%, seat 4 ~14%."""
    g = [0] * 4; w = [0] * 4
    for t in ts:
        for r in t.get("rounds", []):
            for tb in r.get("tables", []):
                ps = tb.get("players", [])
                if len(ps) != 4:
                    continue
                for i, p in enumerate(ps):
                    g[i] += 1
                    w[i] += tb.get("winner_id") == p
    return [w[i] / g[i] if g[i] else BASELINE for i in range(4)]


class Profiler:
    """Per-player evidence: seats and matchups, from their own tournament
    tables plus logged practice games, each shrunk toward the field so small
    samples don't shout."""
    SEAT_PRIOR = 8
    MATCH_PRIOR = 10

    def __init__(self, days: int = 30, min_players: int = 16):
        self.all_ts = tournaments(3650)
        field_ts = tournaments(days, min_players)
        self.field_seats = seat_field(self.all_ts)
        self.pairs = matchup_table(field_ts)
        m = meta(field_ts)["commanders"]
        seats = sum(r["wins"] + r["draws"] + r["losses"] for r in m.values())
        self.base = sum(r["wins"] for r in m.values()) / seats if seats else BASELINE
        self.games = load("games.json")["games"]
        self.prac = practice_stats(self.games)
        self._own = {}

    def own(self, p: dict) -> dict:
        pid = p.get("topdeck_id", "")
        if pid not in self._own:
            self._own[pid] = player_matchups(self.all_ts, pid)
        return self._own[pid]

    def seats(self, p: dict) -> list[dict]:
        """Per seat: games, wins (tournament + practice, 4-player tables) and a
        shrunk estimate compared with what that seat normally wins."""
        g = [0] * 4; w = [0] * 4; src = [[0, 0] for _ in range(4)]
        pid = p.get("topdeck_id")
        if pid:
            for t in self.all_ts:
                for r in t.get("rounds", []):
                    for tb in r.get("tables", []):
                        ps = tb.get("players", [])
                        if len(ps) == 4 and pid in ps:
                            i = ps.index(pid)
                            g[i] += 1; w[i] += tb.get("winner_id") == pid; src[i][0] += 1
        for gm in self.games:
            names = [s["player"] for s in gm["seats"]]
            # only games logged in turn order count toward seat stats
            if gm.get("turn_order", True) and len(names) == 4 and p["name"] in names:
                i = names.index(p["name"])
                g[i] += 1; w[i] += gm["winner"] == p["name"]; src[i][1] += 1
        out = []
        for i in range(4):
            f = self.field_seats[i]
            est = (w[i] + f * self.SEAT_PRIOR) / (g[i] + self.SEAT_PRIOR)
            out.append({"seat": i + 1, "games": g[i], "wins": w[i], "tournament_games": src[i][0],
                        "practice_games": src[i][1], "field": f, "estimate": est, "vs_field": est - f})
        return out

    def matchup(self, p: dict, mine: str, theirs: str) -> tuple[float, int, list[str]]:
        """(estimated win rate - average, games of evidence, evidence lines)."""
        t = self.pairs.get((mine, theirs))
        # the field pairing is itself a sample: shrink it toward average (worth 20 tables)
        tg, tw = (t["games"], t["a_wins"]) if t else (0, 0)
        prior = (tw + self.base * 20) / (tg + 20)
        w = g = 0
        ev = []
        o = self.own(p).get((mine, theirs))
        if o and o["games"]:
            w += o["a_wins"]; g += o["games"]
            ev.append(f"{o['a_wins']}/{o['games']} at tournament tables")
        pr = self.prac.get(p["name"])
        if pr:
            pg, pw = pr["vs_commander"].get((mine, theirs), (0, 0))
            if pg:
                w += pw; g += pg
                ev.append(f"{pw}/{pg} in practice")
        if tg >= 5:
            ev.append(f"{mine.split(',')[0].split(' /')[0]} field-wide {tw / tg * 100:.0f}% over {tg}")
        est = (w + prior * self.MATCH_PRIOR) / (g + self.MATCH_PRIOR)
        return est - self.base, g, ev

    def profile(self, p: dict) -> dict:
        seats = self.seats(p)
        known = [s for s in seats if s["games"] >= 3]
        best = max(known, key=lambda s: s["vs_field"]) if known else None
        worst = min(known, key=lambda s: s["vs_field"]) if len(known) > 1 else None
        decks = [d["commander"] for d in sorted(p.get("decks", []), key=lambda d: not d.get("primary"))]
        rows = []
        opponents = {b for (a, b) in self.own(p) if a in decks}
        pr = self.prac.get(p["name"])
        if pr:
            opponents |= {b for (a, b) in pr["vs_commander"] if a in decks}
        for mine in decks[:2]:
            for theirs in opponents:
                gap, n, ev = self.matchup(p, mine, theirs)
                if n >= 3:
                    rows.append({"mine": mine, "theirs": theirs, "gap": gap, "games": n, "evidence": ev})
        rows.sort(key=lambda r: r["gap"])
        tour = sum(s["tournament_games"] for s in seats)
        prac_n = pr["games"] if pr else 0
        return {"name": p["name"], "decks": decks, "seats": seats,
                "best_seat": best and best["seat"], "worst_seat": worst and worst["seat"],
                "weak": [r for r in rows if r["gap"] < -0.02][:4],
                "strong": [r for r in reversed(rows) if r["gap"] > 0.02][:3],
                "evidence": {"tournament_games": tour, "practice_games": prac_n}}


def practice_stats(games: list[dict]) -> dict:
    """Per player from the group's logged games: record, by commander, versus commanders/players."""
    st = defaultdict(lambda: {"games": 0, "wins": 0, "draws": 0, "by_commander": Counter(),
                              "wins_by_commander": Counter(), "vs_commander": defaultdict(lambda: [0, 0]),
                              "vs_player": defaultdict(lambda: [0, 0])})
    for g in games:
        for s in g["seats"]:
            p = st[s["player"]]
            p["games"] += 1
            p["by_commander"][s["commander"]] += 1
            won = g["winner"] == s["player"]
            if won:
                p["wins"] += 1
                p["wins_by_commander"][s["commander"]] += 1
            if g["winner"] is None:
                p["draws"] += 1
            for o in g["seats"]:
                if o is s:
                    continue
                vc = p["vs_commander"][(s["commander"], o["commander"])]
                vc[0] += 1; vc[1] += won
                vp = p["vs_player"][o["player"]]
                vp[0] += 1; vp[1] += won
    return st


def player_report(names: list[str], days: int) -> list[dict]:
    roster = load("roster.json")
    players = [p for p in roster["players"] if not names or p["name"].lower() in {n.lower() for n in names}]
    ts = sorted(tournaments(days), key=lambda t: t["startDate"], reverse=True)
    prac = practice_stats(load("games.json")["games"])
    out = []
    for p in players:
        events = []
        for t in ts:
            for s in t["standings"]:
                if p.get("topdeck_id") and s.get("id") == p["topdeck_id"]:
                    events.append({"date": fmt_date(t["startDate"]), "event": t.get("tournamentName"),
                                   "players": t["participants"], "standing": s["standing"],
                                   "top_cut": t.get("topCut") or 0, "commander": s.get("leader"),
                                   "record": f"{s.get('wins', 0)}-{s.get('losses', 0)}-{s.get('draws', 0)}",
                                   "wins": s.get("wins") or 0, "losses": s.get("losses") or 0,
                                   "draws": s.get("draws") or 0})
        w = sum(e["wins"] for e in events); l = sum(e["losses"] for e in events); d = sum(e["draws"] for e in events)
        pr = prac.get(p["name"])
        out.append({
            "name": p["name"], "topdeck_id": p.get("topdeck_id"),
            "decks": [d_["commander"] + (" *" if d_.get("primary") else "") for d_ in p.get("decks", [])],
            "tournament": {"events": len(events), "wins": w, "losses": l, "draws": d,
                           "win_rate": w / (w + l + d) if (w + l + d) else None,
                           "top_cuts": sum(1 for e in events if e["top_cut"] and e["standing"] <= e["top_cut"]),
                           "commanders": dict(Counter(e["commander"] for e in events if e["commander"]).most_common()),
                           "recent": events[:8]},
            "practice": None if not pr else {
                "games": pr["games"], "wins": pr["wins"], "draws": pr["draws"],
                "win_rate": pr["wins"] / pr["games"] if pr["games"] else None,
                "by_commander": {c: f"{pr['wins_by_commander'][c]}/{n}" for c, n in pr["by_commander"].most_common()},
            },
        })
    return out


def pct(x):
    return "-" if x is None else f"{x * 100:.1f}%"


def short(c: str) -> str:
    """'Tymna the Weaver / Kraum, Ludevic's Opus' -> 'Tymna/Kraum' for readable lines."""
    return "/".join(part.split(",")[0].split(" the ")[0].strip() for part in c.split(" / "))


def print_profile(pf: dict) -> None:
    ev = pf["evidence"]
    print(f"### {pf['name']}: {', '.join(short(d) for d in pf['decks']) or 'no decks recorded'}")
    print(f"Evidence: {ev['tournament_games']} tournament games at 4-player tables, {ev['practice_games']} practice games.")
    print("Seat (turn order) | games | won | expected for seat | estimate vs seat average")
    for s in pf["seats"]:
        tag = ""
        if s["seat"] == pf["best_seat"]:
            tag = "  <- best seat"
        elif s["seat"] == pf["worst_seat"]:
            tag = "  <- worst seat"
        if tag and s["games"] < 6:
            tag += " (thin sample)"
        rate = f"{s['wins']}/{s['games']}" if s["games"] else "-"
        print(f"  {s['seat']} | {s['games']} | {rate} | {pct(s['field'])} | {s['vs_field'] * 100:+.1f} pts{tag}")
    if not pf["best_seat"]:
        print("  (fewer than 3 games in every seat: no seat read yet)")
    for label, rows in (("Needs practice against", pf["weak"]), ("Strong against", pf["strong"])):
        if rows:
            print(f"{label}:")
            for r in rows:
                print(f"  - vs {short(r['theirs'])} (playing {short(r['mine'])}): {r['gap'] * 100:+.1f} pts vs average "
                      f"({'; '.join(r['evidence'])})")
    if not pf["weak"] and not pf["strong"]:
        print("No matchup has 3+ games of evidence yet. Practice reports will fill this in.")
    print()


def print_meta(m: dict, prev: dict | None, days: int, min_players: int, top: int, area: dict | None = None):
    size = f"{min_players}+ players" if min_players else "all sizes"
    print(f"## cEDH meta: last {days} days, {area_label(area)} ({size})\n")
    print(f"{m['events']} events, {m['total_entries']} entries with a known commander.")
    if area:
        n, located = coverage(days)
        print(f"({located} of {n} events in this window have map coordinates; online events and "
              f"events without a location can't be placed.)")
        if m["total_entries"] < 150:
            print("Small local sample: treat shares as a picture of who shows up, and win rates as hints.")
    print()
    print("| # | Commander | Entries | Meta share | Δ share | Win rate | Draw rate | Top-cut conv. | Best |")
    print("|---|---|---|---|---|---|---|---|---|")
    rows = sorted(m["commanders"].items(), key=lambda kv: -kv[1]["entries"])[:top]
    for i, (c, r) in enumerate(rows, 1):
        delta = "-"
        if prev and prev["total_entries"]:
            ps = prev["commanders"].get(c, {}).get("share", 0)
            delta = f"{(r['share'] - ps) * 100:+.1f}"
        print(f"| {i} | {c} | {r['entries']} | {pct(r['share'])} | {delta} | {pct(r['win_rate'])} | "
              f"{pct(r['draw_rate'])} | {pct(r['conversion'])} | {r['best']} |")
    if not prev or not prev["total_entries"]:
        print("\n(Δ share needs the previous window in the cache: run sync.py with --days "
              f"{days * 2} to see trends.)")
    seats = sum(r["wins"] + r["draws"] + r["losses"] for r in m["commanders"].values())
    avg = sum(r["wins"] for r in m["commanders"].values()) / seats if seats else BASELINE
    print(f"\nAverage win rate per seat in this window is {pct(avg)} (25% before draws).\n\n{ATTRIBUTION}")


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    for name in ("meta", "matchups", "players", "profile"):
        p = sp.add_parser(name)
        p.add_argument("--days", type=int, default=None, help="window (default 30; 90 with --big)")
        p.add_argument("--json", action="store_true")
        if name != "players":
            # default: 16+ for the global meta, every event size for a local one
            p.add_argument("--min-players", type=int, default=None)
        if name in ("meta", "matchups"):
            p.add_argument("--local", action="store_true", help="only events in the saved home area")
            p.add_argument("--zip", help="only events near this zip code")
            p.add_argument("--miles", type=float, help="radius for --zip/--local (default 50, or the saved one)")
            p.add_argument("--big", action="store_true", help="only big events (over 100 players by default)")
    sp.choices["profile"].add_argument("names", nargs="*")
    sp.choices["meta"].add_argument("--top", type=int, default=25)
    sp.choices["matchups"].add_argument("commander", nargs="?")
    sp.choices["matchups"].add_argument("--player", help="one roster player's own tournament tables")
    sp.choices["players"].add_argument("names", nargs="*")
    a = ap.parse_args()
    area = resolve_area(a) if a.cmd in ("meta", "matchups") else None
    if a.days is None:
        # big events are rare (a couple a month), so they need a longer window to say anything
        a.days = 90 if getattr(a, "big", False) else 30
    if getattr(a, "big", False):
        if area:
            die("--big is the big-event meta from anywhere; don't combine it with --local/--zip")
        a.min_players = big_min()
    if a.cmd != "players" and a.min_players is None:
        a.min_players = 0 if area else 16

    if a.cmd == "profile":
        roster = load("roster.json")
        want = {n.lower() for n in a.names}
        players = [p for p in roster["players"] if not want or p["name"].lower() in want]
        prof = Profiler(a.days, a.min_players)
        out = [prof.profile(p) for p in players]
        if a.json:
            print(json.dumps({"field_seats": prof.field_seats, "profiles": out, "attribution": ATTRIBUTION},
                             indent=2, ensure_ascii=False))
            return
        print("Field win rate by seat: " + ", ".join(f"seat {i + 1} {pct(f)}" for i, f in enumerate(prof.field_seats)))
        print("A player's seat is judged against what that seat normally wins, not against 25%.\n")
        for pf in out:
            print_profile(pf)
        print(ATTRIBUTION)
        return

    if a.cmd == "meta":
        m = meta(tournaments(a.days, a.min_players, area=area))
        prev = meta(tournaments(a.days, a.min_players, offset_days=a.days, area=area))
        if a.json:
            print(json.dumps({"area": area, "current": m, "previous": prev, "attribution": ATTRIBUTION}, indent=2))
        else:
            print_meta(m, prev, a.days, a.min_players, a.top, area)

    elif a.cmd == "matchups":
        c = match_commander(a.commander)[0] if a.commander else None
        if a.player:
            p = find_player(load("roster.json"), a.player)
            if not p or not p.get("topdeck_id"):
                die(f"{a.player} is not on the roster with a TopDeck ID")
            # a player's own history: every event size counts, locals included
            pairs = player_matchups(tournaments(a.days), p["topdeck_id"])
            who = p["name"]
        else:
            if not c:
                die("give a commander, or --player NAME")
            pairs = matchup_table(tournaments(a.days, a.min_players, area=area))
            who = None
        rows = [(x, b, v) for (x, b), v in pairs.items() if c is None or x == c]
        rows.sort(key=lambda r: -r[2]["games"])
        if a.json:
            print(json.dumps({"player": who, "commander": c,
                              "vs": [{"mine": x, "theirs": b, **v} for x, b, v in rows],
                              "attribution": ATTRIBUTION}, indent=2))
            return
        title = f"{who} on {c or 'any deck'}" if who else c
        print(f"## {title}: results at tables with each opponent (last {a.days} days)\n")
        print("| Playing | Opponent commander | Games together | Our win % | Their win % | Neither/draw |")
        print("|---|---|---|---|---|---|")
        for x, b, v in rows[:30]:
            g = v["games"]
            print(f"| {x} | {b} | {g} | {pct(v['a_wins'] / g)} | {pct(v['b_wins'] / g)} | {pct(v['other'] / g)} |")
        if not rows:
            print("| (no tables found; check spelling with playgroup.py resolve, or sync more days) | | | | |")
        print(f"\nSmall samples (under ~10 games) are noise. Baseline win rate is {pct(BASELINE)}.\n\n{ATTRIBUTION}")

    else:
        rep = player_report(a.names, a.days)
        if a.json:
            print(json.dumps({"players": rep, "attribution": ATTRIBUTION}, indent=2, ensure_ascii=False))
            return
        for r in rep:
            t = r["tournament"]
            print(f"### {r['name']} (TopDeck {r['topdeck_id'] or 'not linked'})")
            print(f"Decks: {', '.join(r['decks']) or 'none recorded'}")
            print(f"Tournaments, last {a.days} days: {t['events']} events, "
                  f"{t['wins']}-{t['losses']}-{t['draws']} (W-L-D), win rate {pct(t['win_rate'])}, "
                  f"top cuts {t['top_cuts']}")
            if t["commanders"]:
                print("Played: " + ", ".join(f"{c} x{n}" for c, n in t["commanders"].items()))
            for e in t["recent"]:
                print(f"  - {e['date']} {e['event']} ({e['players']}p): #{e['standing']} on "
                      f"{e['commander'] or '(no decklist submitted)'}, {e['record']}")
            if r["practice"]:
                pr = r["practice"]
                print(f"Practice: {pr['wins']}/{pr['games']} wins ({pct(pr['win_rate'])}); "
                      + ", ".join(f"{c} {v}" for c, v in pr["by_commander"].items()))
            print()
        print(ATTRIBUTION)


if __name__ == "__main__":
    main()
