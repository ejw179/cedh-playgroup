"""Roster and group input: players, decks, practice games, notes, availability.

  add-player NAME --topdeck ID_OR_LINK [--deck CMDR ...] [--gauntlet-pilot]
  remove-player NAME
  link NAME --topdeck ID_OR_LINK                # add/replace TopDeck profile for an existing player
  set-decks NAME --deck CMDR [--deck CMDR ...] [--primary CMDR] [--list URL]
  gauntlet-deck add|remove CMDR [--list URL]   # shared meta decks the group owns
  log-game --date D --seat "Player=Commander" x3-5 --winner Player|draw [--note TEXT] [--no-order]
                                                # seats in turn order (seat 1 first)
  home --zip 18201 [--miles 100] [--big-min 101] # --local area, and what counts as a --big event
  note TEXT [--author NAME] [--tag T ...]
  avail DATE [NAME ...] [--replace | --all] [--remove NAME ...]
                                                # e.g. avail 2026-11-08 --all --remove Jeff Saige
  show [roster|games|notes|avail]
  resolve CMDR                                  # check how a commander name matches
"""
import argparse
import json

from common import (die, find_player, load, match_commander, resolve_profile, save, today, topdeck_id,
                    zip_location)


def resolve_or_warn(text: str) -> str:
    best, alts = match_commander(text)
    if alts:
        print(f"  '{text}' -> '{best}' (also matched: {', '.join(alts)})")
    elif best.lower() != text.lower():
        print(f"  '{text}' -> '{best}'")
    return best


def cmd_add_player(a):
    roster = load("roster.json")
    if find_player(roster, a.name):
        die(f"{a.name} is already on the roster; use set-decks to change decks")
    prof = {"id": "", "handle": "", "display_name": ""}
    if a.topdeck:
        prof = resolve_profile(a.topdeck) if not a.no_lookup else \
            {"id": topdeck_id(a.topdeck), "handle": "", "display_name": ""}
    tid = prof["id"]
    if tid and any(p.get("topdeck_id") == tid for p in roster["players"]):
        die(f"TopDeck ID {tid} already belongs to another player")
    decks = [{"commander": resolve_or_warn(d), "primary": i == 0} for i, d in enumerate(a.deck or [])]
    roster["players"].append({"name": a.name, "topdeck_id": tid, "topdeck_handle": prof["handle"],
                              "topdeck_name": prof["display_name"], "decks": decks,
                              "gauntlet_pilot": a.gauntlet_pilot, "added": today()})
    save("roster.json", roster)
    shown = f"{tid}" + (f", {prof['handle']}" if prof["handle"] else "") + \
        (f", TopDeck name '{prof['display_name']}'" if prof["display_name"] else "")
    print(f"Added {a.name} (TopDeck ID: {shown or 'none'}) with {len(decks)} deck(s).")


def cmd_link(a):
    """Attach (or replace) a TopDeck profile on someone already on the roster."""
    roster = load("roster.json")
    p = find_player(roster, a.name)
    if not p:
        die(f"no player named {a.name}")
    prof = resolve_profile(a.topdeck)
    other = next((x for x in roster["players"] if x is not p and x.get("topdeck_id") == prof["id"]), None)
    if other:
        die(f"TopDeck ID {prof['id']} already belongs to {other['name']}")
    p.update({"topdeck_id": prof["id"], "topdeck_handle": prof["handle"], "topdeck_name": prof["display_name"]})
    save("roster.json", roster)
    print(f"Linked {p['name']} to TopDeck ID {prof['id']}"
          + (f" ({prof['handle']})" if prof["handle"] else "")
          + (f", TopDeck name '{prof['display_name']}'" if prof["display_name"] else ""))


def cmd_remove_player(a):
    roster = load("roster.json")
    p = find_player(roster, a.name)
    if not p:
        die(f"no player named {a.name}")
    roster["players"].remove(p)
    save("roster.json", roster)
    print(f"Removed {p['name']} from the roster (their logged games are kept).")


def cmd_set_decks(a):
    roster = load("roster.json")
    p = find_player(roster, a.name)
    if not p:
        die(f"no player named {a.name}")
    decks = [resolve_or_warn(d) for d in a.deck]
    primary = resolve_or_warn(a.primary) if a.primary else decks[0]
    if a.add:
        have = {d["commander"] for d in p["decks"]}
        p["decks"] += [{"commander": d, "primary": False} for d in decks if d not in have]
    else:
        p["decks"] = [{"commander": d, "primary": False} for d in decks]
    for d in p["decks"]:
        d["primary"] = d["commander"] == primary
        if a.list and d["commander"] == primary:
            d["list"] = a.list
    if a.gauntlet_pilot is not None:
        p["gauntlet_pilot"] = a.gauntlet_pilot == "yes"
    save("roster.json", roster)
    print(f"{p['name']} decks: " + ", ".join(d["commander"] + (" (primary)" if d["primary"] else "") for d in p["decks"]))


def cmd_gauntlet(a):
    roster = load("roster.json")
    c = resolve_or_warn(a.commander)
    g = roster.setdefault("gauntlet_decks", [])
    if a.action == "add":
        if not any(d["commander"] == c for d in g):
            g.append({"commander": c, **({"list": a.list} if a.list else {})})
    else:
        roster["gauntlet_decks"] = [d for d in g if d["commander"] != c]
    save("roster.json", roster)
    print("Gauntlet decks: " + (", ".join(d["commander"] for d in roster["gauntlet_decks"]) or "none"))


def cmd_log_game(a):
    roster = load("roster.json")
    seats = []
    for s in a.seat:
        if "=" not in s:
            die(f"seat '{s}' should look like Player=Commander")
        who, cmdr = (x.strip() for x in s.split("=", 1))
        p = find_player(roster, who)
        if not p:
            print(f"  note: {who} is not on the roster; logging as a guest")
        seats.append({"player": p["name"] if p else who, "commander": resolve_or_warn(cmdr)})
    if not 3 <= len(seats) <= 5:
        die("a pod needs 3 to 5 seats")
    winner = None
    if a.winner.lower() != "draw":
        names = [s["player"].lower() for s in seats]
        if a.winner.lower() not in names:
            die(f"winner {a.winner} is not seated in this game")
        winner = seats[names.index(a.winner.lower())]["player"]
    games = load("games.json")
    games["games"].append({"date": a.date or today(), "seats": seats, "winner": winner,
                           "turn_order": not a.no_order, "note": a.note or ""})
    save("games.json", games)
    print(f"Logged game #{len(games['games'])} on {a.date or today()}"
          + (" (turn order unknown)" if a.no_order else " (seats in turn order)") + ": "
          + ", ".join(f"{i}. {s['player']} ({s['commander']})" for i, s in enumerate(seats, 1))
          + f" -> {'draw' if winner is None else winner + ' won'}")


def cmd_home(a):
    """Save the group's home area so `--local` means 'near us'."""
    cfg = load("config.json")
    if a.zip:
        loc = zip_location(a.zip)
        cfg["home"] = {**loc, "miles": a.miles or 50}
    elif a.miles and cfg.get("home"):
        cfg["home"]["miles"] = a.miles
    elif not cfg.get("home") and not a.big_min:
        die("no home area yet: give --zip")
    if a.big_min:
        cfg["big_min_players"] = a.big_min
    save("config.json", cfg)
    h = cfg.get("home")
    if h:
        print(f"Home area (--local): within {h['miles']:g} miles of {h['zip']} ({h['place']}).")
    print(f"Big events (--big): {cfg.get('big_min_players', 101)}+ players, anywhere.")


def cmd_note(a):
    notes = load("notes.json")
    notes["notes"].append({"date": today(), "author": a.author or "", "text": a.text, "tags": a.tag or []})
    save("notes.json", notes)
    print(f"Saved note #{len(notes['notes'])}.")


def resolve_names(roster, names):
    out = []
    for n in names or []:
        p = find_player(roster, n)
        if not p:
            die(f"{n} is not on the roster (roster: {', '.join(x['name'] for x in roster['players'])})")
        out.append(p["name"])
    return out


def cmd_avail(a):
    """Set who's playing on a date. Attendance varies, so this supports
    'everyone', 'everyone except', adding and dropping people."""
    av = load("availability.json")
    roster = load("roster.json")
    if a.all:
        cur = [p["name"] for p in roster["players"]]
    elif a.replace:
        cur = []
    else:
        cur = av["sessions"].get(a.date, [])
    cur = set(cur) | set(resolve_names(roster, a.names))
    cur -= set(resolve_names(roster, a.remove))
    av["sessions"][a.date] = sorted(cur)
    save("availability.json", av)
    out = av["sessions"][a.date]
    missing = [p["name"] for p in roster["players"] if p["name"] not in cur]
    print(f"{a.date}: {', '.join(out) or 'nobody'} ({len(out)} players)")
    if missing:
        print(f"  not playing: {', '.join(missing)}")


def cmd_show(a):
    what = a.what or "roster"
    name = {"roster": "roster.json", "games": "games.json", "notes": "notes.json",
            "avail": "availability.json"}[what]
    print(json.dumps(load(name), indent=2, ensure_ascii=False))


def cmd_resolve(a):
    best, alts = match_commander(a.commander)
    print(json.dumps({"input": a.commander, "best": best, "alternatives": alts}, indent=2))


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)

    p = sp.add_parser("add-player"); p.set_defaults(f=cmd_add_player)
    p.add_argument("name"); p.add_argument("--topdeck", default="")
    p.add_argument("--deck", action="append"); p.add_argument("--gauntlet-pilot", action="store_true")
    p.add_argument("--no-lookup", action="store_true", help="store the ID as given; skip reading the profile page")

    p = sp.add_parser("remove-player"); p.set_defaults(f=cmd_remove_player); p.add_argument("name")

    p = sp.add_parser("link"); p.set_defaults(f=cmd_link)
    p.add_argument("name"); p.add_argument("--topdeck", required=True)

    p = sp.add_parser("set-decks"); p.set_defaults(f=cmd_set_decks)
    p.add_argument("name"); p.add_argument("--deck", action="append", required=True)
    p.add_argument("--primary"); p.add_argument("--list"); p.add_argument("--add", action="store_true")
    p.add_argument("--gauntlet-pilot", choices=["yes", "no"])

    p = sp.add_parser("gauntlet-deck"); p.set_defaults(f=cmd_gauntlet)
    p.add_argument("action", choices=["add", "remove"]); p.add_argument("commander"); p.add_argument("--list")

    p = sp.add_parser("log-game"); p.set_defaults(f=cmd_log_game)
    p.add_argument("--date"); p.add_argument("--seat", action="append", required=True)
    p.add_argument("--winner", required=True); p.add_argument("--note")
    p.add_argument("--no-order", action="store_true",
                   help="seats were NOT given in turn order (game still counts for matchups, not seat stats)")

    p = sp.add_parser("home"); p.set_defaults(f=cmd_home)
    p.add_argument("--zip"); p.add_argument("--miles", type=float)
    p.add_argument("--big-min", type=int, help="player count that makes an event 'big' (default 101)")

    p = sp.add_parser("note"); p.set_defaults(f=cmd_note)
    p.add_argument("text"); p.add_argument("--author"); p.add_argument("--tag", action="append")

    p = sp.add_parser("avail"); p.set_defaults(f=cmd_avail)
    p.add_argument("date"); p.add_argument("names", nargs="*")
    p.add_argument("--replace", action="store_true", help="start the list fresh from NAMES")
    p.add_argument("--all", action="store_true", help="start from the whole roster")
    p.add_argument("--remove", nargs="+", metavar="NAME", help="drop these people (e.g. --all --remove Jeff)")

    p = sp.add_parser("show"); p.set_defaults(f=cmd_show)
    p.add_argument("what", nargs="?", choices=["roster", "games", "notes", "avail"])

    p = sp.add_parser("resolve"); p.set_defaults(f=cmd_resolve); p.add_argument("commander")

    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
