"""Post newly planned pods and newly reported results to Discord.

Runs in the import job after import_results.py, discord_replies.py,
plan_sessions.py and session.py advance (and by /start-session right after it
saves a session): whatever those added since the last commit is posted to the
group's channel. The group asked for these to go out automatically; everything
else still goes through discord.py preview / send.

Pods go out as a header plus one message per pod. Each pod message tags its
players (DISCORD_TAGS secret) and says why each seat and deck was chosen;
players reply to it with how the game went, which discord_replies.py reads.
The ids of those messages are saved in pods.json for that.

  python announce.py              # compare data/ with git HEAD and post what's new
  python announce.py --dry-run    # print the posts instead
"""
import argparse
import json
import subprocess
import time
from datetime import date

from analyze import short
from common import data_dir, load, save, secret
from discord import CREDIT, WEBHOOK, chunks, post, tags

SITE = "https://ejw179.github.io/cedh-playgroup"


def at_head(name: str, ref: str) -> dict:
    """data/<name> as of the last commit ({} if it didn't exist)."""
    path = (data_dir() / name).resolve()
    try:
        root = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=path.parent,
                              capture_output=True, text=True, check=True).stdout.strip()
        rel = path.relative_to(root).as_posix()
        out = subprocess.run(["git", "show", f"{ref}:{rel}"], cwd=root, capture_output=True, text=True,
                             encoding="utf-8", check=True).stdout
        return json.loads(out)
    except (subprocess.CalledProcessError, ValueError):
        return {}


def nice(day: str) -> str:
    d = date.fromisoformat(day)
    return f"{d:%a %b} {d.day}"


def who(s: dict, tag: dict) -> str:
    t = tag.get(s["player"].lower())
    return f"{s['player']}{' (random)' if s.get('guest') else ''}{f' <@{t}>' if t else ''}"


def seat_reason(s: dict) -> str:
    """One line on why this player is here, on this deck: seat read, deck rank, the matchup to work on."""
    out = []
    read = s.get("seat_read") or ""
    if read in ("worst seat", "best seat"):
        out.append(f"{read} for them ({s['seat_record']} here, "
                   f"{s['seat_vs_field'] * 100:+.0f} pts vs the seat average)")
    elif read:
        out.append(read)
    if s.get("practice_rank"):
        out.append(f"practice deck #{s['practice_rank']}")
    if s.get("gauntlet"):
        out.append("gauntlet deck")
    weak = [h for h in s.get("matchups_here", []) if h["gap"] < 0]
    if weak:
        h = weak[0]
        out.append(f"needs reps vs {h['opponent']}'s {short(h['commander'])} ({h['gap'] * 100:+.0f} pts)")
    return "; ".join(out)


def header_post(p: dict) -> str:
    label = (p.get("scenario") or {}).get("label") or "Practice"
    games = p.get("games", 1)
    game = f"Game {p.get('game', 1)} of {games} · " if games > 1 else ""
    lines = [f"**{game}{nice(p['date'])}** · {label}"]
    if p.get("goal"):
        lines.append(f"🎯 {p['goal']}")
    rules = " · ".join(x for x in (f"{p['time_limit']} min" if p.get("time_limit") else "", p.get("rules", "")) if x)
    if rules:
        lines.append(f"⏱ {rules}")
    if p.get("game", 1) > 1:
        lines.append("Re-planned after the last game: nobody sits in a seat they've already had today.")
    if p.get("bench"):
        lines.append(f"Sitting out this game: {', '.join(p['bench'])}")
    lines.append(f"Seat 1 goes first. Full reasons and the report form: <{SITE}/#current>")
    if p.get("analysis", True):
        lines.append(CREDIT)
    return "\n".join(lines)


def pod_post(p: dict, pod: dict, tag: dict) -> str:
    analysis = p.get("analysis", True)
    lines = [f"**Pod {pod['pod']}**"]
    for s in pod["seats"]:
        lines.append(f"{s['seat']}. {who(s, tag)} — {short(s['commander'])}")
        why = seat_reason(s) if analysis else ""
        if why:
            lines.append(f"-# {why}")
    if pod.get("meta_decks"):
        lines.append(f"Meta decks at the table: {'; '.join(short(m.split(' (')[0]) for m in pod['meta_decks'])}")
    for w in pod.get("warnings", []):
        lines.append(f"⚠️ {w}")
    lines.append("↩️ **After the game, reply to this message**: who won (or draw), and your take on the game.")
    return "\n".join(lines)


def result_post(g: dict, sessions: dict, tag: dict) -> str:
    s = sessions.get(g["date"]) or {}
    game = f", game {g['game']} of {s.get('games', 1)}" if g.get("game") and s.get("games", 1) > 1 else ""
    pod = f", pod {g['pod']}" if g.get("pod") else ""
    win = next((x for x in g["seats"] if x["player"] == g["winner"]), None)
    lines = [f"**Result · {nice(g['date'])}{game}{pod}**",
             f"🏆 {who(win, tag)} won on {short(win['commander'])}" if win else "🤝 Draw"]
    lines.append(" · ".join(f"{i}. {x['player']} ({short(x['commander'])})" for i, x in enumerate(g["seats"], 1)))
    if g.get("note"):
        lines.append(f"📝 {g['note']}")
    notes = g.get("notes") or {}
    if notes:
        lines.append("\n**How each player saw it**")
        for name, note in notes.items():
            lines.append(f"> **{name}:** {note}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print the posts instead of sending")
    ap.add_argument("--ref", default="HEAD", help="git ref to compare with (default: the last commit)")
    a = ap.parse_args()

    old_sessions = at_head("pods.json", a.ref).get("sessions", {})
    old_games = {g.get("source") for g in at_head("games.json", a.ref).get("games", [])}
    saved = load("pods.json")
    tag = tags()
    # (text, ping these user ids, session day, pod no.) -- results first, then the next pods
    posts = []
    for g in load("games.json")["games"]:
        if g.get("source") and g["source"] not in old_games:
            posts.append((result_post(g, saved["sessions"], tag), [], None, None))
    for day, p in sorted(saved["sessions"].items()):
        if p.get("saved_at") == (old_sessions.get(day) or {}).get("saved_at") or p.get("done"):
            continue
        p = {**p, "date": day}
        posts.append((header_post(p), [], None, None))
        for pod in p.get("pods", []):
            ids = [tag[s["player"].lower()] for s in pod["seats"] if s["player"].lower() in tag]
            posts.append((pod_post(p, pod, tag), ids, day, pod["pod"]))
    if not posts:
        print("Nothing new to announce.")
        return

    url = secret("DISCORD_WEBHOOK_URL")
    if not a.dry_run and not (url and WEBHOOK.match(url)):
        print("DISCORD_WEBHOOK_URL isn't set (or isn't a Discord webhook); not posting.")
        a.dry_run = True
    sent = 0
    for text, ping, day, pod in posts:
        for m in chunks(text):
            if a.dry_run:
                print(m + "\n-----")
                continue
            msg = post(url, {"content": m, "username": "cEDH Playgroup",
                             "allowed_mentions": {"parse": [], "users": ping}}, wait=day is not None)
            if day and msg and msg.get("id"):  # players reply to this message; discord_replies.py reads it
                s = saved["sessions"][day]
                s.setdefault("discord", {}).setdefault("posts", []).append(
                    {"game": s.get("game", 1), "pod": pod, "id": msg["id"], "channel": msg.get("channel_id")})
                sent += 1
            time.sleep(1)
    if sent:
        save("pods.json", saved)
    print(f"{'Would post' if a.dry_run else 'Posted'} {len(posts)} update(s) to Discord.")


if __name__ == "__main__":
    main()
