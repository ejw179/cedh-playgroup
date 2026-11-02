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

from analyze import BASELINE, Profiler, meta, practice_stats, short, tournaments
from common import ATTRIBUTION, die, find_player, load, match_commander


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

        ts = tournaments(a.days, a.min_players)
        m = meta(ts)["commanders"]
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
        self.prof = Profiler(a.days, a.min_players)
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
            forced[p["name"]] = match_commander(c)[0]
        gauntlet = [d["commander"] for d in roster.get("gauntlet_decks", [])]
        self.options = {}
        for p in self.players:
            own = [d["commander"] for d in sorted(p.get("decks", []), key=lambda d: not d.get("primary"))]
            if p["name"] in forced:
                opts = [(forced[p["name"]], "own")]
            else:
                opts = [(c, "own") for c in own] + ([(c, "gauntlet") for c in gauntlet] if p.get("gauntlet_pilot") else [])
            if not opts:
                opts = [("(deck not recorded)", "own")]
            self.options[p["name"]] = opts
        self.primary = {p["name"]: self.options[p["name"]][0][0] for p in self.players}
        # 0 = primary, 1 = second deck, ...; gauntlet decks count as 1 (a fair ask of a pilot)
        self.deck_rank = {n: {c: (i if kind == "own" else 1) for i, (c, kind) in enumerate(opts)}
                          for n, opts in self.options.items()}
        self.focus = {f.lower() for f in (a.focus or [])}

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
        if mode == "random":
            order = pod[:]; rng.shuffle(order); return order

        def val(n, i):
            s = self.seats[n][i]
            conf = min(1.0, s["games"] / 6)
            mult = 4.0 if n.lower() in self.focus else 1.0
            v = -s["vs_field"] if mode == "practice" else s["vs_field"]
            return v * conf * mult

        best = max(itertools.permutations(pod), key=lambda perm: (sum(val(n, i) for i, n in enumerate(perm)), rng.random()))
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
            # weak matchups
            for n in pod:
                mine = decks[n][0]
                mult = 4.0 if n.lower() in self.focus else 1.0
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date"); ap.add_argument("--players", nargs="*")
    ap.add_argument("--all", action="store_true", help="everyone on the roster")
    ap.add_argument("--except", dest="exclude", nargs="+", metavar="NAME", help="leave these people out")
    ap.add_argument("--deck", action="append"); ap.add_argument("--focus", action="append")
    ap.add_argument("--w-meta", type=float, default=1.0); ap.add_argument("--w-balance", type=float, default=1.0)
    ap.add_argument("--w-repeat", type=float, default=1.0); ap.add_argument("--w-matchup", type=float, default=1.0)
    ap.add_argument("--top-meta", type=int, default=15); ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--min-players", type=int, default=16)
    ap.add_argument("--seed", type=int, default=1); ap.add_argument("--restarts", type=int, default=25)
    ap.add_argument("--iterations", type=int, default=400); ap.add_argument("--json", action="store_true")
    ap.add_argument("--seat-mode", choices=["practice", "best", "random"], default="practice",
                    help="practice: seat players where they're weakest; best: rehearse best seats")
    a = ap.parse_args()

    m = Model(a)
    pods, decks, notes, bench = m.solve()
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
                "evidence": pf["evidence"], "player_strength": round(m.strength[n], 3)})
        result["pods"].append({
            "pod": i, "seats": seats, "strength": round(pn["strength"], 3), "meta_decks": pn["meta"],
            "repeat_pairings": pn["repeats"], "warnings": pn["warnings"]})
    if a.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    print("Seat = turn order. Field win rate by seat: "
          + ", ".join(f"{i + 1}: {f * 100:.1f}%" for i, f in enumerate(m.prof.field_seats))
          + f". Seating mode: {a.seat_mode}.\n")
    for p in result["pods"]:
        print(f"### Pod {p['pod']} (strength {p['strength']:.3f})")
        for s in p["seats"]:
            print(f"Seat {s['seat']}: {s['player']} on {short(s['commander'])}"
                  + (" [gauntlet deck]" if s["gauntlet"] else ""))
            ev = s["evidence"]
            print(f"  evidence: {ev['tournament_games']} tournament + {ev['practice_games']} practice games")
            print(f"  seat: {s['seat_read']} ({s['seat_record']} here, {s['seat_vs_field'] * 100:+.1f} pts vs "
                  f"seat average {s['seat_expected'] * 100:.1f}%)")
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
