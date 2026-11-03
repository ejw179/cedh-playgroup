"""Post newly planned pods and newly reported results to Discord.

Runs in the import job after import_results.py and plan_sessions.py: whatever
those added since the last commit (a session planned on the site, a pod result
reported on the site) is posted to the group's channel. The group asked for
these two kinds to go out automatically; everything else still goes through
discord.py preview / send.

  python announce.py              # compare data/ with git HEAD and post what's new
  python announce.py --dry-run    # print the posts instead
"""
import argparse
import json
import subprocess
import time
from datetime import date

from analyze import short
from common import data_dir, load, secret
from discord import CREDIT, WEBHOOK, chunks, post

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


def who(s: dict) -> str:
    return f"{s['player']}{' (random)' if s.get('guest') else ''}"


def pods_post(p: dict) -> str:
    label = (p.get("scenario") or {}).get("label") or "Practice"
    lines = [f"**Pods for {nice(p['date'])}** · {label}"]
    for pod in p.get("pods", []):
        lines.append(f"\n**Pod {pod['pod']}**")
        lines += [f"{s['seat']}. {who(s)} — {short(s['commander'])}" for s in pod["seats"]]
    if p.get("bench"):
        lines.append(f"\nSitting out first game: {', '.join(p['bench'])}")
    lines.append(f"\nSeat 1 goes first. Reasons and the report form: <{SITE}/#sessions>")
    if p.get("analysis", True):
        lines.append(CREDIT)
    return "\n".join(lines)


def result_post(g: dict) -> str:
    pod = f", pod {g['pod']}" if g.get("pod") else ""
    win = next((s for s in g["seats"] if s["player"] == g["winner"]), None)
    lines = [f"**Result · {nice(g['date'])}{pod}**",
             f"🏆 {who(win)} won on {short(win['commander'])}" if win else "🤝 Draw"]
    lines.append(" · ".join(f"{i}. {who(s)} ({short(s['commander'])})" for i, s in enumerate(g["seats"], 1)))
    for name, note in (g.get("notes") or {}).items():
        lines.append(f"> **{name}:** {note}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print the posts instead of sending")
    ap.add_argument("--ref", default="HEAD", help="git ref to compare with (default: the last commit)")
    a = ap.parse_args()

    old_sessions = at_head("pods.json", a.ref).get("sessions", {})
    old_games = {g.get("source") for g in at_head("games.json", a.ref).get("games", [])}
    posts = []
    for day, p in sorted(load("pods.json")["sessions"].items()):
        if p.get("saved_at") != (old_sessions.get(day) or {}).get("saved_at"):
            posts.append(pods_post({**p, "date": day}))
    for g in load("games.json")["games"]:
        if g.get("source") and g["source"] not in old_games:
            posts.append(result_post(g))
    if not posts:
        print("Nothing new to announce.")
        return

    url = secret("DISCORD_WEBHOOK_URL")
    if not a.dry_run and not (url and WEBHOOK.match(url)):
        print("DISCORD_WEBHOOK_URL isn't set (or isn't a Discord webhook); not posting.")
        a.dry_run = True
    for text in posts:
        for m in chunks(text):
            if a.dry_run:
                print(m + "\n-----")
            else:
                post(url, {"content": m, "username": "cEDH Playgroup", "allowed_mentions": {"parse": []}})
                time.sleep(1)
    print(f"{'Would post' if a.dry_run else 'Posted'} {len(posts)} update(s) to Discord.")


if __name__ == "__main__":
    main()
