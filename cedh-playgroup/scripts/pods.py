"""Build practice pods for a session.

  python pods.py --date 2026-11-08                 # players from availability
  python pods.py --players Eric Sam Lee Kim Jo     # or name them
  options: --deck "Sam=Kinnan" (force a deck)  --focus Eric (target Eric's weak matchups)
           --w-meta/--w-balance/--w-repeat/--w-matchup (0 turns a goal off)
           --top-meta 15 --days 30 --min-players 16 --seed 1 --json

Each player plays one of their own decks (primary preferred). Players
flagged gauntlet_pilot may instead take one of the group's shared gauntlet
decks, which is how top-meta decks nobody owns get into pods.
"""
import argparse
import itertools
import json
import math
import random
from collections import defaultdict
from datetime import datetime, timezone

from analyze import (BASELINE, Profiler, area_label, big_min, meta, practice_stats, resolve_area, short,
                     takeaways, tournaments)
from common import ATTRIBUTION, die, find_player, load, match_commander, save


def pod_sizes(n: int) -> tuple[list[int], int]:
    """Fours where possible, threes to absorb the remainder. Returns (sizes, benched)."""
    if n < 3:
        die("need at least 3 players for a pod")
    if n == 5:
        return [4], 1
    r = n % 4
    threes = {0: 0, 1: 3, 2: 2, 3: 1}[r]
    fours = (n - 3 * threes) // 4
    return [4] * fours + [3] * threes, 0


def smooth(wins, games, k=12):
    return (wins + BASELINE * k) / (games + k)


class Model:
    def __init__(self, a):
        self.a = a
        roster = load("roster.json")
        games = load("games.json")["games"]
        # who's playing: --players beats the saved list for --date; --all is the whole roster
        if a.players:
            names = a.players
        elif a.date and load("availability.json")["sessions"].get(a.date):
            names = load("availability.json")["sessions"][a.date]
        elif a.all:
            names = [p["name"] for p in roster["players"]]
        else:
            die(f"nobody is marked available for {a.date}" if a.date
                else "say who's playing: --players NAMES, --date D (saved availability), or --all")
        drop = set()
        for n in a.exclude or []:
            p = find_player(roster, n)
            if not p:
                die(f"--except: {n} is not on the roster")
            drop.add(p["name"])
        self.players = []
        for n in names:
            p = find_player(roster, n)
            if not p:
                die(f"{n} is not on the roster")
            if p["name"] not in drop and p not in self.players:
                self.players.append(p)

        # "top meta" can be the local scene (--local / --zip); matchups and seats stay global for sample size
        self.area = resolve_area(a)
        floor = a.min_players if a.min_players is not None else (big_min() if a.big else 0 if self.area else 16)
        self.meta_label = (f"big events ({floor}+ players)" if a.big and not self.area else area_label(self.area))
        ts = tournaments(a.days, floor, area=self.area)
        m = meta(ts)["commanders"]
        self.meta_rows = m
        top = sorted(m.items(), key=lambda kv: -kv[1]["entries"])[: a.top_meta]
        self.meta_rank = {c: i + 1 for i, (c, _) in enumerate(top)}
        self.share = {c: r["share"] for c, r in top}
        # average win rate per seat once draws are counted (a bit under 25%)
        wins = sum(r["wins"] for r in m.values())
        seats = sum(r["wins"] + r["draws"] + r["losses"] for r in m.values())
        self.base = wins / seats if seats else BASELINE
        self.deck_wr = {c: smooth(r["wins"], r["wins"] + r["draws"] + r["losses"]) for c, r in m.items()}
        self.prac = practice_stats(games)

        # player strength: tournament record (all cached history) blended with practice
        all_ts = tournaments(3650)
        self.strength = {}
        for p in self.players:
            w = g = 0
            for t in all_ts:
                for s in t["standings"]:
                    if p.get("topdeck_id") and s.get("id") == p["topdeck_id"]:
                        w += s.get("wins") or 0
                        g += (s.get("wins") or 0) + (s.get("losses") or 0) + (s.get("draws") or 0)
            pr = self.prac.get(p["name"])
            pw, pg = (pr["wins"], pr["games"]) if pr else (0, 0)
            self.strength[p["name"]] = smooth(w + pw, g + pg)

        # per-player evidence: seats and matchups from their own tables + practice
        self.prof = Profiler(a.days, 16)  # field matchups: global 16+ events, for sample size
        self.by_name = {p["name"]: p for p in self.players}
        self.profiles = {p["name"]: self.prof.profile(p) for p in self.players}
        self.seats = {n: pf["seats"] for n, pf in self.profiles.items()}
        self._weak = {}

        # recency-weighted "sat together" counts from logged games
        self.together = defaultdict(float)
        for k, g in enumerate(reversed(games[-40:])):
            wgt = 0.85 ** k
            ps = [s["player"] for s in g["seats"]]
            for i in range(len(ps)):
                for j in range(i + 1, len(ps)):
                    self.together[frozenset((ps[i], ps[j]))] += wgt

        # deck options per player
        forced = {}
        for f in a.deck or []:
            who, c = f.split("=", 1)
            p = find_player(roster, who)
            if not p:
                die(f"--deck: {who} is not on the roster")
            mine = [d["commander"] for d in p.get("decks", []) + p.get("practice_decks", [])]
            forced[p["name"]] = match_commander(c, mine)[0]
        # fix-a-matchup targets: "Player=Commander" -> that deck should sit at their table
        self.fix = {}
        for f in getattr(a, "fix", None) or []:
            who, c = f.split("=", 1)
            p = find_player(roster, who)
            if not p:
                die(f"--fix: {who} is not on the roster")
            self.fix[p["name"]] = match_commander(c)[0]
        pool = getattr(a, "deck_pool", "all")
        gauntlet = [d["commander"] for d in roster.get("gauntlet_decks", [])]
        self.options = {}
        self.decklist = {}
        for p in self.players:
            # practice decks (the ones they asked for reps on) lead, in their priority order
            practice = [d["commander"] for d in p.get("practice_decks", [])]
            rest = [d["commander"] for d in sorted(p.get("decks", []), key=lambda d: not d.get("primary"))]
            own = practice + [c for c in rest if c not in practice]
            if pool == "practice" and practice:
                own = practice  # scenarios play from the player's 1-3 practice decks
            elif pool == "practice":
                own = rest[:3]
            self.decklist[p["name"]] = {d["commander"]: d["list"] for d in p.get("decks", []) + p.get("practice_decks", [])
                                        if d.get("list")}
            if p["name"] in forced:
                opts = [(forced[p["name"]], "own")]
            else:
                pilots = p.get("gauntlet_pilot") and (pool == "all" or self.fix)
                opts = [(c, "own") for c in own] + ([(c, "gauntlet") for c in gauntlet] if pilots else [])
                if not opts:
                    opts = [("(deck not recorded)", "own")]
                if getattr(a, "first_deck", False) or p["name"].lower() in {x.lower() for x in getattr(a, "first_deck_for", [])}:
                    opts = opts[:1]  # "#1 deck" scenarios
            self.options[p["name"]] = opts
        self.primary = {p["name"]: self.options[p["name"]][0][0] for p in self.players}
        # 0 = primary, 1 = second deck, ...; gauntlet decks count as 1 (a fair ask of a pilot)
        # big-event scenario: each player brings whichever of their decks does best at big events
        if getattr(a, "best_meta_deck", False):
            for n, opts in self.options.items():
                if n not in forced and len(opts) > 1:
                    self.options[n] = [max(opts, key=lambda o: self.deck_wr.get(o[0], BASELINE))]
        self.deck_rank = {n: {c: (i if kind == "own" else 1) for i, (c, kind) in enumerate(opts)}
                          for n, opts in self.options.items()}
        self.focus = {f.lower() for f in (a.focus or [])}
        # fixed seats: "Player=N" (turn order within their pod)
        self.fixed_seat = {}
        for f in getattr(a, "seat", None) or []:
            who, n = f.split("=", 1)
            p = find_player(roster, who)
            if not p or not n.strip().isdigit():
                die(f"--seat: expected Player=N, got {f!r}")
            self.fixed_seat[p["name"]] = int(n) - 1

    def weakness(self, player, mine, theirs):
        """How badly `player` does on `mine` against `theirs`, with the evidence
        (their own tables and practice, shrunk toward the field; see Profiler)."""
        key = (player, mine, theirs)
        if key not in self._weak:
            gap, g, ev = self.prof.matchup(self.by_name[player], mine, theirs)
            # trust it in proportion to evidence: the player's own games, plus a little for field tables
            t = self.prof.pairs.get((mine, theirs))
            conf = min(1.0, (g + (t["games"] if t else 0) * 0.1) / 5)
            self._weak[key] = (max(0.0, -gap) * conf, ev)
        return self._weak[key]

    def seat_order(self, pod, rng):
        """Order a pod's seats (turn order). 'practice' puts players where
        their record is weakest relative to that seat's normal win rate;
        'best' rehearses their strongest seats; 'random' just shuffles."""
        mode = self.a.seat_mode

        def allowed(perm):  # respect "Player=N" fixed seats where the pod is big enough
            return all(perm[i] == n for n, i in self.fixed_seat.items() if n in perm and i < len(perm))

        if mode == "random":
            perms = [p for p in itertools.permutations(pod) if allowed(p)] or [tuple(pod)]
            return list(rng.choice(perms))

        def val(n, i):
            s = self.seats[n][i]
            conf = min(1.0, s["games"] / 6)
            focused = n.lower() in self.focus
            if mode == "focus":  # only the focus player's seat matters; everyone else fits around them
                return (-s["vs_field"] * conf if focused else 0.0) - repeat(n, i)
            mult = 4.0 if focused else 1.0
            v = -s["vs_field"] if mode == "practice" else s["vs_field"]
            return v * conf * mult - repeat(n, i)

        # later games of a multi-game session: don't put anyone back in a seat they already had today
        used = getattr(self.a, "session_seats", None) or {}

        def repeat(n, i):
            return 0.5 * used.get(n, []).count(i + 1)

        perms = [p for p in itertools.permutations(pod) if allowed(p)] or list(itertools.permutations(pod))
        best = max(perms, key=lambda perm: (sum(val(n, i) for i, n in enumerate(perm)), rng.random()))
        return list(best)

    def score(self, pods, decks, explain=False):
        a = self.a
        total = 0.0
        notes = []
        strengths = []
        for pod in pods:
            cmdrs = [decks[n][0] for n in pod]
            pn = {"meta": [], "repeats": [], "matchups": [], "warnings": []}
            # meta exposure: distinct top-meta decks at the table
            for c in set(cmdrs):
                if c in self.share:
                    total += a.w_meta * self.share[c] * 10
                    pn["meta"].append(f"{c} (#{self.meta_rank[c]}, {self.share[c] * 100:.1f}% of meta)")
            if len(set(cmdrs)) < len(cmdrs):
                total -= 50
                pn["warnings"].append("duplicate commander at this table")
            # repeat pairings
            for i in range(len(pod)):
                for j in range(i + 1, len(pod)):
                    t = self.together.get(frozenset((pod[i], pod[j])), 0)
                    if t:
                        total -= a.w_repeat * t * 0.5
                        if t >= 0.8:
                            pn["repeats"].append(f"{pod[i]} & {pod[j]} (recency-weighted {t:.1f})")
            # fix-a-matchup: the target deck must be at that player's table
            for n, target in self.fix.items():
                if n in pod:
                    if any(decks[o][0] == target for o in pod if o != n):
                        total += 25
                    elif explain:
                        pn["warnings"].append(f"couldn't seat {target} with {n}: nobody here brings it")
            # weak matchups
            for n in pod:
                mine = decks[n][0]
                mult = 4.0 if n.lower() in self.focus else (0.0 if getattr(a, "focus_only", False) else 1.0)
                for o in pod:
                    if o == n:
                        continue
                    wk, ev = self.weakness(n, mine, decks[o][0])
                    if wk:
                        total += a.w_matchup * wk * 10 * mult
                        if explain and wk > 0.02:
                            pn["matchups"].append((wk * mult, f"{n}'s {mine} vs {decks[o][0]} ({'; '.join(ev)})"))
            # nudge toward each player's usual deck: the further down their list, the bigger the cost
            for n in pod:
                total -= 0.3 * self.deck_rank[n].get(decks[n][0], 1)
            s = sum(0.5 * self.strength[n] + 0.5 * self.deck_wr.get(decks[n][0], BASELINE) for n in pod) / len(pod)
            strengths.append(s)
            pn["matchups"] = [t for _, t in sorted(pn["matchups"], reverse=True)[:4]]
            notes.append({**pn, "strength": s})
        if len(strengths) > 1:
            mu = sum(strengths) / len(strengths)
            sd = math.sqrt(sum((x - mu) ** 2 for x in strengths) / len(strengths))
            total -= a.w_balance * sd * 60
        return total, notes

    def random_decks(self, rng):
        used = set()
        decks = {}
        for n, opts in self.options.items():
            choices = [o for o in opts if not (o[1] == "gauntlet" and o[0] in used)]
            pick = choices[0] if rng.random() < 0.6 else rng.choice(choices)
            if pick[1] == "gauntlet":
                used.add(pick[0])
            decks[n] = pick
        return decks

    def solve(self):
        rng = random.Random(self.a.seed)
        names = [p["name"] for p in self.players]
        sizes, bench_n = pod_sizes(len(names))
        bench = []
        if bench_n:
            # sit out whoever has played the most logged practice games
            bench = sorted(names, key=lambda n: -(self.prac.get(n, {}) or {}).get("games", 0))[:bench_n]
            names = [n for n in names if n not in bench]
        if getattr(self.a, "random_all", False):
            # random pods, and a random deck from each player's options (seats are shuffled in seat_order)
            order = names[:]
            rng.shuffle(order)
            pods, i = [], 0
            for sz in sizes:
                pods.append(order[i:i + sz]); i += sz
            decks = {n: rng.choice(self.options[n]) for n in names}
            _, notes = self.score(pods, decks, explain=True)
            return pods, decks, notes, bench
        best = None
        for _ in range(self.a.restarts):
            order = names[:]
            rng.shuffle(order)
            pods, i = [], 0
            for sz in sizes:
                pods.append(order[i:i + sz]); i += sz
            decks = self.random_decks(rng)
            cur, _ = self.score(pods, decks)
            for _ in range(self.a.iterations):
                np_ = [p[:] for p in pods]; nd = dict(decks)
                if len(np_) > 1 and rng.random() < 0.6:
                    a_, b_ = rng.sample(range(len(np_)), 2)
                    i_, j_ = rng.randrange(len(np_[a_])), rng.randrange(len(np_[b_]))
                    np_[a_][i_], np_[b_][j_] = np_[b_][j_], np_[a_][i_]
                else:
                    n = rng.choice(names)
                    taken = {d[0] for k, d in nd.items() if d[1] == "gauntlet" and k != n}
                    opts = [o for o in self.options[n] if not (o[1] == "gauntlet" and o[0] in taken)]
                    nd[n] = rng.choice(opts)
                s, _ = self.score(np_, nd)
                if s >= cur:
                    pods, decks, cur = np_, nd, s
            if best is None or cur > best[0]:
                best = (cur, pods, decks)
        _, pods, decks = best
        _, notes = self.score(pods, decks, explain=True)
        return pods, decks, notes, bench


def parser():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date"); ap.add_argument("--players", nargs="*")
    ap.add_argument("--all", action="store_true", help="everyone on the roster")
    ap.add_argument("--except", dest="exclude", nargs="+", metavar="NAME", help="leave these people out")
    ap.add_argument("--deck", action="append"); ap.add_argument("--focus", action="append")
    ap.add_argument("--w-meta", type=float, default=1.0); ap.add_argument("--w-balance", type=float, default=1.0)
    ap.add_argument("--w-repeat", type=float, default=1.0); ap.add_argument("--w-matchup", type=float, default=1.0)
    ap.add_argument("--top-meta", type=int, default=15); ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--min-players", type=int, default=None)
    ap.add_argument("--local", action="store_true", help="use the home area's meta for 'face the top meta'")
    ap.add_argument("--zip"); ap.add_argument("--miles", type=float)
    ap.add_argument("--big", action="store_true", help="use the big-event meta for 'face the top meta'")
    ap.add_argument("--seed", type=int, default=1); ap.add_argument("--restarts", type=int, default=25)
    ap.add_argument("--iterations", type=int, default=400); ap.add_argument("--json", action="store_true")
    ap.add_argument("--seat-mode", choices=["practice", "best", "random", "focus"], default="practice",
                    help="practice: seat players where they're weakest; best: rehearse best seats")
    ap.add_argument("--save", action="store_true", help="lock in this seating for --date (shown on the site)")
    ap.add_argument("--scenario", choices=list(SCENARIOS), help="a preset reason for the pods (see SCENARIOS)")
    ap.add_argument("--fix", action="append", metavar="PLAYER=CMDR", help="put that deck at the player's table")
    ap.add_argument("--seat", action="append", metavar="PLAYER=N", help="fix a player's seat (turn order)")
    ap.add_argument("--first-deck", action="store_true", help="everyone plays their #1 practice deck (\"best decks\")")
    ap.add_argument("--manual", help='pods built by hand: JSON pods in seat order, e.g. [[{"player":"Eric","commander":"Kinnan"},{"player":"Sam","guest":true,"commander":"Tymna"},...]]')
    return ap


# The reasons a session can be built for. Each sets the deck pool, seat mode and goal weights.
SCENARIOS = {
    "improve": "Improve one player",
    "worst_seat": "Worst-seat practice",
    "rehearsal": "Tournament rehearsal",
    "local": "Face the local meta",
    "big": "Face big-event meta",
    "fix": "Fix a matchup",
    "specific": "Specific deck and seat",
    "generic": "Build pods manually",
    "random": "Random",
}


def apply_scenario(a) -> None:
    """Translate --scenario into the builder's knobs. Explicit flags still win where they're set."""
    s = a.scenario
    if not s:
        return
    a.deck_pool = "practice"
    if s == "improve":
        if not a.focus:
            die("improve: pick the player with --focus")
        a.focus = a.focus[:1]
        a.seat_mode, a.focus_only = "focus", True  # their worst seat, their weak matchups; others fit around
        a.first_deck_for = a.focus
        a.w_meta, a.w_balance, a.w_repeat, a.w_matchup = 0.3, 0.3, 0.3, 2.0
    elif s == "worst_seat":
        a.seat_mode, a.first_deck = "practice", True
        a.w_meta = 0.5
    elif s == "rehearsal":
        # everyone on their #1 deck in their best seat; pods as even as possible so anyone can win
        a.seat_mode, a.first_deck = "best", True
        a.w_balance, a.w_matchup, a.w_meta, a.w_repeat = 3.0, 0.0, 0.0, 0.5
    elif s == "local":
        a.local, a.seat_mode = True, "practice"
        a.w_meta, a.w_matchup = 3.0, 0.3
    elif s == "big":
        a.big, a.seat_mode, a.best_meta_deck = True, "practice", True
        a.w_meta = 1.0
    elif s == "fix":
        if not a.fix:
            die("fix: say whose matchup with --fix PLAYER=CMDR")
        a.deck_pool = "all"  # the target may be someone's non-practice deck or a gauntlet deck
        a.focus = [f.split("=", 1)[0] for f in a.fix]
        a.seat_mode, a.w_meta = "practice", 0.3
    elif s == "specific":
        a.seat_mode = "practice"
    elif s == "random":
        a.seat_mode, a.random_all = "random", True


def meta_gaps(m: "Model", roster: dict, top_n: int = 12) -> dict:
    """Top decks in this scenario's meta that nobody in the group brings (practice decks, decks or gauntlet)."""
    group = {d["commander"] for p in roster["players"] for d in p.get("decks", []) + p.get("practice_decks", [])}
    group |= {d["commander"] for d in roster.get("gauntlet_decks", [])}
    rows = sorted(m.meta_rows.items(), key=lambda kv: -kv[1]["entries"])[:top_n]
    missing = []
    for c, r in rows:
        if c in group:
            continue
        strong = (r["win_rate"] or 0) >= m.base and (r["conversion"] or 0) >= 0.25
        missing.append({"commander": c, "share": round(r["share"], 3), "win_rate": r["win_rate"] and round(r["win_rate"], 3),
                        "top_cut_rate": r["conversion"] and round(r["conversion"], 3), "major": strong})
    covered = [c for c, _ in rows if c in group]
    return {"lens": m.meta_label, "missing": missing, "covered": covered, "top_n": top_n}


def manual_plan(a) -> dict:
    """Pods built by hand: the group picks every seat's player (roster or a random/guest) and deck; no analysis."""
    roster = load("roster.json")
    try:
        pods_in = json.loads(a.manual)
    except json.JSONDecodeError as e:
        die(f"--manual isn't valid JSON: {e}")
    pods, seen = [], set()
    for i, pod in enumerate(pods_in, 1):
        seats = []
        for k, s in enumerate(pod, 1):
            name = " ".join(str(s.get("player") or "").split())[:40]
            p = find_player(roster, name) if name else None
            if not p and not s.get("guest"):
                die(f"--manual: unknown player {s.get('player')!r}")
            if not p:  # a random / guest who isn't on the roster
                p = {"name": name or f"Random {sum(1 for x in seen if x.startswith('Random')) + 1}", "guest": True}
            if p["name"] in seen:
                die(f"--manual: {p['name']} is in two seats")
            seen.add(p["name"])
            mine = [d["commander"] for d in p.get("decks", []) + p.get("practice_decks", [])]
            cmdr = match_commander(str(s.get("commander") or ""), mine)[0] if s.get("commander") else "(deck not recorded)"
            lists = {d["commander"]: d.get("list") for d in p.get("decks", []) + p.get("practice_decks", []) if d.get("list")}
            seats.append({"seat": k, "player": p["name"], "commander": cmdr, "gauntlet": False,
                          **({"guest": True} if p.get("guest") else {}),
                          "decklist": lists.get(cmdr, ""), "evidence": {"tournament_games": 0, "practice_games": 0},
                          "seat_read": "", "seat_record": "", "seat_vs_field": 0, "seat_expected": 0,
                          "matchups_here": [], "needs_practice_elsewhere": [], "practice_rank": None, "takeaway": None})
        if not 3 <= len(seats) <= 5:
            die(f"--manual: pod {i} needs 3 to 5 players")
        pods.append({"pod": i, "seats": seats, "strength": None, "meta_decks": [], "repeat_pairings": [], "warnings": []})
    return {"field_seats": [], "seat_mode": "manual", "pods": pods, "bench": [], "meta_label": "",
            "analysis": False, "attribution": ATTRIBUTION}


def plan(a) -> tuple["Model | None", dict]:
    """Build pods for parsed args `a`; returns the model and the JSON-able result.
    Used by main() and by build_site.py."""
    if a.scenario == "generic" or a.manual:
        result = manual_plan(a)
        result["scenario"] = {"id": "generic", "label": SCENARIOS["generic"]}
        return None, result
    apply_scenario(a)
    m = Model(a)
    pods, decks, notes, bench = m.solve()
    games = load("games.json")["games"]
    rng = random.Random(a.seed)
    result = {"field_seats": m.prof.field_seats, "seat_mode": a.seat_mode, "pods": [], "bench": bench,
              "attribution": ATTRIBUTION}
    for i, (pod, pn) in enumerate(zip(pods, notes), 1):
        order = m.seat_order(pod, rng)
        seats = []
        for k, n in enumerate(order):
            pf = m.profiles[n]
            s = m.seats[n][k]
            if s["games"] >= 3 and k + 1 == pf["worst_seat"]:
                seat_why = "worst seat"
            elif s["games"] >= 3 and k + 1 == pf["best_seat"]:
                seat_why = "best seat"
            else:
                seat_why = "no strong read" if s["games"] >= 3 else "not enough games in this seat yet"
            here = []
            for o in order:
                if o == n:
                    continue
                gap, g, ev = m.prof.matchup(m.by_name[n], decks[n][0], decks[o][0])
                if g and abs(gap) > 0.02:
                    here.append({"opponent": o, "commander": decks[o][0], "gap": round(gap, 3), "games": g, "evidence": ev})
            here.sort(key=lambda h: h["gap"])
            seated = {decks[o][0] for o in order}
            elsewhere = [w for w in pf["weak"] if w["mine"] == decks[n][0] and w["theirs"] not in seated]
            seats.append({
                "seat": k + 1, "player": n, "commander": decks[n][0], "gauntlet": decks[n][1] == "gauntlet",
                "seat_record": f"{s['wins']}/{s['games']}", "seat_expected": round(s["field"], 3),
                "seat_vs_field": round(s["vs_field"], 3), "seat_read": seat_why,
                "matchups_here": here, "needs_practice_elsewhere": [
                    {"commander": w["theirs"], "gap": round(w["gap"], 3), "evidence": w["evidence"]} for w in elsewhere[:2]],
                "evidence": pf["evidence"], "player_strength": round(m.strength[n], 3),
                "decklist": m.decklist[n].get(decks[n][0], ""),
                "practice_rank": next((r for r, d in enumerate(m.by_name[n].get("practice_decks", []), 1)
                                       if d["commander"] == decks[n][0]), None),
                "takeaway": (takeaways(games, n, 1) or [None])[0]})
        result["pods"].append({
            "pod": i, "seats": seats, "strength": round(pn["strength"], 3), "meta_decks": pn["meta"],
            "repeat_pairings": pn["repeats"], "warnings": pn["warnings"]})
    result["meta_label"] = m.meta_label
    sc = a.scenario or "custom"
    result["scenario"] = {"id": sc, "label": SCENARIOS.get(sc, "Custom"),
                          "focus": [p["name"] for p in m.players if p["name"].lower() in m.focus],
                          "fix": [{"player": k, "commander": v} for k, v in m.fix.items()]}
    result["analysis"] = sc != "random"
    if sc in ("local", "big"):
        result["gaps"] = meta_gaps(m, load("roster.json"))
    return m, result


def save_pods(day: str, result: dict) -> None:
    """Lock in a session's seating so the site (and nightly rebuilds) show
    exactly what was posted, and results can be reported against it."""
    saved = load("pods.json")
    saved["sessions"][day] = {**result, "date": day, "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    save("pods.json", saved)


def main():
    a = parser().parse_args()
    m, result = plan(a)
    if a.save:
        if not a.date:
            die("--save needs --date (the session these pods are for)")
        save_pods(a.date, result)
        print(f"Saved pods for {a.date}; the site will show this seating.\n")
    if a.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    bench = result["bench"]
    print(f"Scenario: {result['scenario']['label']}")
    if m is None:  # generic practice: just the seating the group chose
        for p in result["pods"]:
            print(f"### Pod {p['pod']}: " + ", ".join(f"{s['seat']}. {s['player']} ({short(s['commander'])})"
                                                      for s in p["seats"]))
        return
    print("Seat = turn order. Field win rate by seat: "
          + ", ".join(f"{i + 1}: {f * 100:.1f}%" for i, f in enumerate(m.prof.field_seats))
          + f". Seating mode: {a.seat_mode}. Meta used for 'face the top meta': {m.meta_label}.\n")
    if result.get("gaps"):
        g = result["gaps"]
        print(f"Gaps vs {g['lens']} (top {g['top_n']} decks nobody in the group brings): "
              + ("; ".join(f"{short(x['commander'])} {x['share'] * 100:.1f}%" + (" [MAJOR]" if x["major"] else "")
                           for x in g["missing"]) or "none") + "\n")
    for p in result["pods"]:
        print(f"### Pod {p['pod']} (strength {p['strength']:.3f})")
        for s in p["seats"]:
            print(f"Seat {s['seat']}: {s['player']} on {short(s['commander'])}"
                  + (" [gauntlet deck]" if s["gauntlet"] else ""))
            ev = s["evidence"]
            print(f"  evidence: {ev['tournament_games']} tournament + {ev['practice_games']} practice games")
            print(f"  seat: {s['seat_read']} ({s['seat_record']} here, {s['seat_vs_field'] * 100:+.1f} pts vs "
                  f"seat average {s['seat_expected'] * 100:.1f}%)")
            if s.get("practice_rank"):
                print(f"  practice deck #{s['practice_rank']}" + (f" ({s['decklist']})" if s.get("decklist") else ""))
            if s.get("takeaway"):
                t = s["takeaway"]
                print(f"  last takeaway ({t['date']}): {t['text']}")
            for h in s["matchups_here"]:
                verb = "needs practice vs" if h["gap"] < 0 else "favoured vs"
                print(f"  {verb} {h['opponent']}'s {short(h['commander'])}: {h['gap'] * 100:+.1f} pts "
                      f"({'; '.join(h['evidence'])})")
            for w in s["needs_practice_elsewhere"]:
                print(f"  also weak vs {short(w['commander'])} (not at this table): {w['gap'] * 100:+.1f} pts "
                      f"({'; '.join(w['evidence'])})")
        if p["meta_decks"]:
            print("Meta decks at the table: " + "; ".join(p["meta_decks"]))
        if p["repeat_pairings"]:
            print("Recent repeat pairings (unavoidable at this size): " + "; ".join(p["repeat_pairings"]))
        for w in p["warnings"]:
            print(f"WARNING: {w}")
        print()
    if bench:
        print(f"Sitting out first round: {', '.join(bench)} (5 players: rotate them in next game).")
    print(ATTRIBUTION)


if __name__ == "__main__":
    main()
