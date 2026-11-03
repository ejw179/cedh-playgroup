"""Build and save pods for sessions requested on the website.

The site's "Plan a session" form posts {type: "session", date, players,
seats, meta, focus} to the same Apps Script endpoint as results. This reads
new requests, records who's playing, builds the pods with pods.py, and saves
them so the site shows that seating with its report form. If several
requests name the same date, the latest one wins.

  python plan_sessions.py               # uses results_url from config.json
  python plan_sessions.py --file dump.json
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone

import pods as podmod
from common import find_player, load, save
from import_results import fetch

SEATS = {"practice": "practice", "best": "best", "random": "random"}
META = {"local", "big", "global", "none"}


def args_for(req: dict, roster: dict, has_home: bool) -> tuple[list[str] | None, dict, str]:
    """pods.py arguments for one request plus {players, focus}, or (None, {}, problem)."""
    p = req.get("payload") or {}
    day = str(p.get("date") or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return None, {}, f"bad date {day!r}"
    if p.get("scenario") == "generic" and p.get("pods"):
        return manual_args(p, day, roster)
    names = []
    for n in p.get("players") or []:
        pl = find_player(roster, str(n))
        if not pl:
            return None, {}, f"unknown player {n!r}"
        if pl["name"] not in names:
            names.append(pl["name"])
    if len(names) < 3:
        return None, {}, "needs at least 3 players"
    scenario = str(p.get("scenario") or "")
    if scenario:
        return scenario_args(p, day, names, scenario, roster)
    # older requests: seats + meta lens
    seats = SEATS.get(str(p.get("seats") or "practice"))
    lens = str(p.get("meta") or "local")
    if not seats or lens not in META:
        return None, {}, f"unknown scenario {p.get('seats')!r}/{lens!r}"
    argv = ["--date", day, "--players", *names, "--seat-mode", seats, "--json"]
    if lens == "local" and has_home:
        argv.append("--local")
    elif lens == "big":
        argv.append("--big")
    elif lens == "none":
        argv += ["--w-meta", "0"]
    focus = []
    for n in p.get("focus") or []:
        pl = find_player(roster, str(n))
        if pl and pl["name"] in names and pl["name"] not in focus:
            focus.append(pl["name"])
            argv += ["--focus", pl["name"]]
    return argv, {"players": names, "focus": focus, "seats": seats, "meta": lens}, ""


def scenario_args(p: dict, day: str, names: list[str], scenario: str, roster: dict):
    """A request built around one of pods.SCENARIOS, with per-player choices."""
    if scenario not in podmod.SCENARIOS:
        return None, {}, f"unknown scenario {scenario!r}"
    argv = ["--date", day, "--players", *names, "--scenario", scenario, "--json"]
    info = {"players": names, "scenario": scenario}

    def player(n):
        pl = find_player(roster, str(n or ""))
        return pl["name"] if pl and pl["name"] in names else None

    # per-player choices: {player, commander?, seat?, pod?}
    picks = []
    for c in p.get("assign") or []:
        who = player(c.get("player"))
        if who:
            seat = str(c.get("seat") or "")
            pod = str(c.get("pod") or "")
            picks.append({"player": who, "commander": str(c.get("commander") or "").strip(),
                          "seat": int(seat) if seat.isdigit() else None, "pod": int(pod) if pod.isdigit() else None})
    if scenario == "improve":
        who = player(p.get("player"))
        if not who:
            return None, {}, "improve: pick the player to focus on"
        argv += ["--focus", who]
        info["focus"] = [who]
    elif scenario == "fix":
        who, target = player(p.get("player")), str(p.get("target") or "").strip()
        if not who or not target:
            return None, {}, "fix: pick the player and the deck to face"
        argv += ["--fix", f"{who}={target}"]
        info["fix"] = {"player": who, "commander": target}
    elif scenario == "generic":
        # the group picks everything: pod, seat and deck for every player
        if len(picks) != len(names) or any(x["pod"] is None or x["seat"] is None or not x["commander"] for x in picks):
            return None, {}, "generic: every player needs a pod, a seat and a deck"
        pods = {}
        for x in sorted(picks, key=lambda x: (x["pod"], x["seat"])):
            pods.setdefault(x["pod"], []).append({"player": x["player"], "commander": x["commander"]})
        argv = ["--date", day, "--scenario", "generic", "--json",
                "--manual", json.dumps([pods[k] for k in sorted(pods)])]
        return argv, info, ""
    for x in picks:  # "specific" (and any scenario): honour a chosen deck and/or seat
        if x["commander"]:
            argv += ["--deck", f"{x['player']}={x['commander']}"]
        if x["seat"]:
            argv += ["--seat", f"{x['player']}={x['seat']}"]
    return argv, info, ""


def manual_args(p: dict, day: str, roster: dict):
    """Pods built by hand on the site: [[{player, guest?, commander?}, ...], ...] in seat order.
    Roster players and randoms/guests (anyone not on the roster) can sit anywhere."""
    pods, known, seen = [], [], set()
    for i, pod in enumerate(p.get("pods") or [], 1):
        seats = []
        for s in pod or []:
            name = " ".join(str(s.get("player") or "").split())[:40]
            pl = find_player(roster, name) if name else None
            if not pl and not s.get("guest"):
                return None, {}, f"manual: unknown player {name!r}"
            if pl:
                name = pl["name"]
                if name not in known:
                    known.append(name)
            if name and name.lower() in seen:
                return None, {}, f"manual: {name} is in two seats"
            seen.add(name.lower())
            seats.append({"player": name, "commander": str(s.get("commander") or "").strip()[:120],
                          **({} if pl else {"guest": True})})
        if not 3 <= len(seats) <= 5:
            return None, {}, f"manual: pod {i} needs 3 to 5 seats"
        pods.append(seats)
    if not pods:
        return None, {}, "manual: no pods"
    argv = ["--date", day, "--scenario", "generic", "--json", "--manual", json.dumps(pods)]
    return argv, {"players": known, "scenario": "generic"}, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="JSON from the endpoint, instead of fetching")
    ap.add_argument("--pending", action="store_true",
                    help="only check: exit 0 if there are new requests, 1 if not (lets CI skip the TopDeck sync)")
    a = ap.parse_args()
    if a.file:
        with open(a.file, encoding="utf-8") as f:
            subs = json.load(f).get("results", [])
    else:
        url = load("config.json").get("results_url")
        if not url:
            print("No results_url in config.json yet; nothing to plan.")
            sys.exit(1 if a.pending else 0)
        subs = fetch(url)

    cfg = load("config.json")
    roster = load("roster.json")
    saved = load("pods.json")
    done = set(saved.get("planned_requests", [])) | set(cfg.get("ignore_submissions", []))
    todo = [s for s in subs if (s.get("payload") or {}).get("type") == "session" and s.get("id") not in done]
    if a.pending:
        print(f"{len(todo)} new session request(s).")
        sys.exit(0 if todo else 1)
    planned = 0
    for req in sorted(todo, key=lambda s: s.get("received", "")):
        argv, info, problem = args_for(req, roster, bool(cfg.get("home")))
        saved.setdefault("planned_requests", []).append(req["id"])
        if not argv:
            print(f"Skipped session request {req['id']}: {problem}")
            continue
        p = req["payload"]
        av = load("availability.json")
        if info["players"]:
            av["sessions"][p["date"]] = sorted(info["players"])
            save("availability.json", av)
        try:
            _, result = podmod.plan(podmod.parser().parse_args(argv))
        except SystemExit as e:
            print(f"Skipped session request {req['id']}: pod builder stopped ({e})")
            continue
        saved["sessions"][p["date"]] = {
            **result, "date": p["date"], "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "requested_by": str(p.get("reporter") or "")[:60],
            "scenario": result.get("scenario") if "scenario" in info else
            {"id": "custom", "label": "Custom", "seats": info["seats"], "meta": info["meta"], "focus": info["focus"]},
        }
        planned += 1
        print(f"Planned {p['date']}: " + " | ".join(
            ", ".join(f"{s['seat']}. {s['player']}" for s in pod["seats"]) for pod in result["pods"]))
    if todo:
        save("pods.json", saved)
    print(f"{planned} session(s) planned.")


if __name__ == "__main__":
    main()
