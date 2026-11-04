"""Register (or update) the playgroup's slash commands in the group's Discord server.

Run once after creating the app, and again whenever the commands below change:

  python discord-bot/register.py            # needs DISCORD_BOT_TOKEN, DISCORD_APP_ID, DISCORD_GUILD_ID
  python discord-bot/register.py --print    # just show the JSON

Guild commands show up right away (global ones can take an hour), and only in that server.
What each command does lives in worker.js; this is just their names and options.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "cedh-playgroup" / "scripts"))
from common import die, secret  # noqa: E402

GOALS = {  # same ids and labels as GOALS in worker.js (pods.py SCENARIOS)
    "worst_seat": "Worst-seat practice",
    "rehearsal": "Tournament rehearsal (best seats)",
    "local": "Face the local meta",
    "big": "Face big-event meta",
    "improve": "Improve one player (needs player)",
    "fix": "Fix a matchup (needs player and target)",
    "random": "Random",
}
STR, INT, BOOL = 3, 4, 5

COMMANDS = [
    {"name": "start", "description": "Plan a session: pods, decks and seats from the group's data",
     "options": [
         {"type": STR, "name": "players", "required": True,
          "description": "Who's playing: names or @mentions, e.g. Eric, Darison, Alex, Jeff"},
         {"type": STR, "name": "goal", "required": True, "description": "What the games are for",
          "choices": [{"name": v, "value": k} for k, v in GOALS.items()]},
         {"type": INT, "name": "games", "description": "How many games (default 1)", "min_value": 1, "max_value": 10},
         {"type": BOOL, "name": "best_decks", "description": "Everyone plays their #1 practice deck"},
         {"type": STR, "name": "player", "description": "Improve / Fix: who it's for", "autocomplete": True},
         {"type": STR, "name": "target", "description": "Fix: the commander they need to face"},
         {"type": STR, "name": "objective", "description": "Extra words for the goal, e.g. Stress-test politics.",
          "max_length": 200},
         {"type": INT, "name": "time_limit", "description": "Minutes per game", "min_value": 10, "max_value": 240},
         {"type": STR, "name": "rules", "description": "e.g. win or draw", "max_length": 100},
         {"type": STR, "name": "date", "description": "Default today; 2026-11-22 or 11/22"},
         {"type": BOOL, "name": "replace", "description": "Start over if that day already has a session"},
     ]},
    {"name": "report", "description": "Report your pod's game: who won (or Draw), plus your take",
     "options": [
         {"type": STR, "name": "winner", "required": True, "description": "A seated player, or Draw",
          "autocomplete": True},
         {"type": STR, "name": "note", "description": "Your take on the game (saved as your note)", "max_length": 600},
         {"type": INT, "name": "pod", "description": "Which pod, if it isn't clear from the winner or you",
          "min_value": 1, "max_value": 10},
     ]},
    {"name": "pods", "description": "Show the seating of the game being played now"},
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", action="store_true", help="print the commands instead of registering them")
    a = ap.parse_args()
    if a.print:
        print(json.dumps(COMMANDS, indent=2))
        return
    token, app, guild = secret("DISCORD_BOT_TOKEN"), secret("DISCORD_APP_ID"), secret("DISCORD_GUILD_ID")
    if not (token and app and guild):
        die("set DISCORD_BOT_TOKEN, DISCORD_APP_ID and DISCORD_GUILD_ID (see discord-bot/README.md)")
    req = urllib.request.Request(
        f"https://discord.com/api/v10/applications/{app}/guilds/{guild}/commands",
        data=json.dumps(COMMANDS).encode(), method="PUT",
        headers={"Authorization": f"Bot {token}", "Content-Type": "application/json",
                 "User-Agent": "DiscordBot (https://github.com/ejw179/cedh-playgroup, 1.0)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            names = [c["name"] for c in json.load(r)]
    except urllib.error.HTTPError as e:
        hint = " (invite the bot with the applications.commands scope)" if e.code == 403 else ""
        die(f"Discord said HTTP {e.code}{hint}: {e.read().decode(errors='replace')[:300]}")
    print("Registered: " + ", ".join("/" + n for n in names))


if __name__ == "__main__":
    main()
