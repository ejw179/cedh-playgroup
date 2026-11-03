"""Multi-game sessions: plan game 1 now, then the next game each time one is reported.

  python session.py start --date 2026-11-22 --games 3 --goal "Tournament practice: best decks, worst seat. Stress-test politics." \\
      --time-limit 80 --rules "win or draw" -- --scenario worst_seat --players Eric Darison Alex Jeff
  python session.py advance             # plan the next game of every session whose current game is fully reported
  python session.py advance --pending   # exit 0 if some session needs its next game planned, else 1
  python session.py status              # open sessions and where they stand

Everything after `--` is passed to pods.py (scenario, players, decks, seats...). The
session is saved in pods.json under its date, so the site, the Discord posts and
the report forms all show the current game. Later games keep the pods' decks
(tournament style) and move players to seats they haven't had yet today; a
manual session ("--scenario generic --manual ...") just rotates its seats.
"""
import argparse
import json
import sys
from datetime import datetime, timezone

import pods as podmod
from common import die, load, save


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def game_no(s: dict) -> int:
    return s.get("game", 1)


def total(s: dict) -> int:
    return s.get("games", 1)


def results_for(day: str, game: int, games: list[dict]) -> list[dict]:
    """Reported games for one game number of a session (results without a number count as game 1)."""
    return [g for g in games if g["date"] == day and g.get("game", 1) == game]


def pod_result(day: str, game: int, pod: int, games: list[dict]) -> dict | None:
    return next((g for g in results_for(day, game, games) if g.get("pod", 1) == pod), None)


def is_open(s: dict) -> bool:
    return not s.get("done")


def seats_so_far(s: dict) -> dict:
    """Seat numbers each player has had in this session, including the current game."""
    used = {}
    for g in s.get("history", []) + [{"pods": s.get("pods", [])}]:
        for pod in g["pods"]:
            for seat in pod["seats"]:
                used.setdefault(seat["player"], []).append(seat["seat"])
    return used


def plan_game(s: dict) -> dict:
    """Seating for the session's next game."""
    if (s.get("scenario") or {}).get("id") == "generic":
        # a hand-built session: same tables, everyone moves one seat (seat 1 goes last)
        out = []
        for pod in s["pods"]:
            seats = pod["seats"][1:] + pod["seats"][:1]
            out.append({**pod, "seats": [{**x, "seat": k} for k, x in enumerate(seats, 1)]})
        return {"pods": out}
    a = podmod.parser().parse_args(s["plan_args"] + ["--date", s["date"]])
    a.session_seats = seats_so_far(s)
    if s.get("keep_decks", True):  # tournament style: the deck you registered all day
        a.deck = [f"{x['player']}={x['commander']}" for pod in s["pods"] for x in pod["seats"]]
    _, result = podmod.plan(a)
    return result


def start(a, pod_args: list[str]) -> dict:
    if not a.date:
        die("start needs --date")
    sessions = load("pods.json")["sessions"]
    old = sessions.get(a.date)
    if old and is_open(old) and old.get("history") and not a.replace:
        die(f"{a.date} already has a session in progress (game {game_no(old)} of {total(old)}); "
            "pass --replace to start over")
    pa = podmod.parser().parse_args(pod_args + ["--date", a.date])
    _, result = podmod.plan(pa)
    s = {**result, "date": a.date, "saved_at": now(), "game": 1, "games": max(1, a.games),
         "goal": a.goal or "", "time_limit": a.time_limit, "rules": a.rules or "",
         "plan_args": pod_args, "keep_decks": not a.new_decks, "history": [],
         **({"requested_by": a.by} if a.by else {})}
    saved = load("pods.json")
    saved["sessions"][a.date] = s
    save("pods.json", saved)
    return s


def advance(dry: bool = False) -> list[str]:
    """Close fully reported games: plan the next one, or mark the session done."""
    saved = load("pods.json")
    games = load("games.json")["games"]
    changed = []
    for day, s in sorted(saved["sessions"].items()):
        if not is_open(s) or not s.get("pods"):
            continue
        k = game_no(s)
        if not all(pod_result(day, k, pod["pod"], games) for pod in s["pods"]):
            continue
        if k >= total(s):
            changed.append(f"{day}: all {total(s)} game(s) reported; session done")
            if not dry:
                s["done"] = True
            continue
        changed.append(f"{day}: game {k} reported; planning game {k + 1} of {total(s)}")
        if dry:
            continue
        nxt = plan_game({**s, "date": day})
        s.setdefault("history", []).append({"game": k, "pods": s["pods"], "bench": s.get("bench", []),
                                            "saved_at": s.get("saved_at")})
        s.update(nxt)
        s["game"], s["saved_at"] = k + 1, now()
    if changed and not dry:
        save("pods.json", saved)
    return changed


def describe(day: str, s: dict, games: list[dict]) -> str:
    k = game_no(s)
    lines = [f"{day} · game {k} of {total(s)} · {(s.get('scenario') or {}).get('label', 'Custom')}"
             + ("" if is_open(s) else " · done")]
    if s.get("goal"):
        lines.append(f"  goal: {s['goal']}")
    if s.get("time_limit") or s.get("rules"):
        lines.append("  " + " · ".join(x for x in (f"{s['time_limit']} min" if s.get("time_limit") else "",
                                                     s.get("rules", "")) if x))
    for pod in s.get("pods", []):
        r = pod_result(day, k, pod["pod"], games)
        state = ("reported: " + ("draw" if r["winner"] is None else f"{r['winner']} won")) if r else "awaiting result"
        lines.append(f"  Pod {pod['pod']} ({state}): " + ", ".join(
            f"{x['seat']}. {x['player']} ({podmod.short(x['commander'])})" for x in pod["seats"]))
    if s.get("bench"):
        lines.append(f"  sitting out: {', '.join(s['bench'])}")
    return "\n".join(lines)


def main():
    argv = sys.argv[1:]
    pod_args = []
    if "--" in argv:
        i = argv.index("--")
        argv, pod_args = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("start")
    p.add_argument("--date"); p.add_argument("--games", type=int, default=1)
    p.add_argument("--goal", help="what the session is for, in the group's words (shown on the site and Discord)")
    p.add_argument("--time-limit", type=int, help="minutes per game")
    p.add_argument("--rules", help='e.g. "win or draw" (no intentional draws, a game at time is a draw)')
    p.add_argument("--by", help="who planned it")
    p.add_argument("--new-decks", action="store_true", help="let later games pick decks again (default: keep them)")
    p.add_argument("--replace", action="store_true", help="start over even if a session that day is in progress")
    p.add_argument("--json", action="store_true")
    p = sp.add_parser("advance")
    p.add_argument("--pending", action="store_true", help="only check: exit 0 if a session needs its next game")
    p = sp.add_parser("status")
    p.add_argument("--date"); p.add_argument("--all", action="store_true", help="include finished sessions")
    a = ap.parse_args(argv)

    if a.cmd == "start":
        s = start(a, pod_args)
        if a.json:
            print(json.dumps(s, indent=2, ensure_ascii=False))
        else:
            print(f"Saved. {describe(a.date, s, load('games.json')['games'])}")
    elif a.cmd == "advance":
        todo = advance(dry=a.pending)
        for line in todo:
            print(line)
        if a.pending:
            sys.exit(0 if any("planning" in t for t in todo) else 1)
        if not todo:
            print("No session is waiting on its next game.")
    else:
        games = load("games.json")["games"]
        rows = [(d, s) for d, s in sorted(load("pods.json")["sessions"].items())
                if (not a.date or d == a.date) and (a.all or a.date or is_open(s))]
        print("\n\n".join(describe(d, s, games) for d, s in rows) or "No open sessions.")


if __name__ == "__main__":
    main()
