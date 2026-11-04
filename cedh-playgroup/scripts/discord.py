"""Post to the group's Discord channel through a channel webhook.

Two steps, on purpose. A post is public, so what gets sent is exactly what
the user saw and approved:

  python discord.py preview --kind pods --file draft.md
      checks and splits the draft, adds the TopDeck credit if needed, and saves
      it to <data>/outbox/. Prints the messages and the outbox path.
  python discord.py send <outbox file>
      posts that saved file, unchanged. Run only after the user says yes.

Kinds: pods, meta, results, availability, other.
Webhook URL: DISCORD_WEBHOOK_URL (env var, or the saved Windows user variable).
"""
import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from common import ATTRIBUTION, data_dir, die, load, now_unix, secret

LIMIT = 1900  # Discord's cap is 2000 characters per message; leave headroom
USES_TOPDECK = {"pods", "meta", "results"}
CREDIT = "-# Tournament data from TopDeck.gg (<https://topdeck.gg>)"
WEBHOOK = re.compile(r"^(https://(?:(?:canary|ptb)\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+"
                     r"|http://(?:127\.0\.0\.1|localhost)(?::\d+)?/\S*)$")


def chunks(text: str) -> list[str]:
    """Split on blank lines, then lines, so no message breaks mid-sentence."""
    out, cur = [], ""
    for block in re.split(r"\n{2,}", text.strip()):
        pieces = [block] if len(block) <= LIMIT else block.split("\n")
        for piece in pieces:
            while len(piece) > LIMIT:  # a single huge line: hard cut
                out.append(piece[:LIMIT]); piece = piece[LIMIT:]
            sep = "\n\n" if block is piece or piece == pieces[0] else "\n"
            if cur and len(cur) + len(sep) + len(piece) > LIMIT:
                out.append(cur); cur = piece
            else:
                cur = f"{cur}{sep}{piece}" if cur else piece
    if cur:
        out.append(cur)
    return out


def cmd_preview(a):
    text = Path(a.file).read_text(encoding="utf-8").strip() if a.file else sys.stdin.read().strip()
    if not text:
        die("the draft is empty")
    warnings = []
    if re.search(r"^\s*\|.*\|\s*$\n^\s*\|[\s:|-]+\|\s*$", text, re.M):
        warnings.append("contains a markdown table; Discord shows tables as raw pipes. Use lists instead.")
    if re.search(r"@(everyone|here)\b", text):
        warnings.append("contains @everyone/@here; it will NOT ping (mentions are disabled) unless --allow-mentions")
    if a.kind in USES_TOPDECK and "topdeck.gg" not in text.lower():
        text += "\n\n" + CREDIT
    parts = chunks(text)

    outbox = data_dir() / "outbox"
    outbox.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = outbox / f"{stamp}-{a.kind}.json"
    body = {"kind": a.kind, "messages": parts, "allow_mentions": a.allow_mentions,
            "created": now_unix(), "username": a.username}
    body["sha"] = hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:12]
    path.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")

    for i, p in enumerate(parts, 1):
        print(f"----- message {i}/{len(parts)} ({len(p)} chars) -----")
        print(p)
    print("-----")
    for w in warnings:
        print(f"WARNING: {w}")
    print(f"Saved for sending: {path}")
    print("Show this to the user. Send only after they approve: python discord.py send \"%s\"" % path)


def tags() -> dict[str, str]:
    """Player name (lower case) -> Discord user ID. Players link themselves with Discord's /iam,
    which saves discord_id on the roster; the DISCORD_TAGS secret ("Eric=123456789012345678,
    Alex=234567890123456789", or the same as a JSON object) fills in anyone not linked yet."""
    out = {}
    raw = secret("DISCORD_TAGS")
    if raw:
        try:
            if raw.lstrip().startswith("{"):
                pairs = json.loads(raw).items()
            else:
                pairs = [x.split("=", 1) for x in re.split(r"[,;\n]", raw) if "=" in x]
            out = {k.strip().lower(): str(v).strip() for k, v in pairs if str(v).strip().isdigit()}
        except ValueError:
            print("DISCORD_TAGS isn't valid; using the roster's Discord links only.", file=sys.stderr)
    for p in load("roster.json").get("players", []):
        if str(p.get("discord_id") or "").isdigit():
            out[p["name"].lower()] = p["discord_id"]
    return out


def post(url: str, payload: dict, wait: bool = False) -> dict | None:
    """Send one webhook message. With wait=True, Discord returns the message (its id is what
    players reply to)."""
    if wait:
        url += ("&" if "?" in url else "?") + "wait=true"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "cedh-playgroup-skill (webhook, 1.0)"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read()
                return json.loads(body) if wait and body else None
        except urllib.error.HTTPError as e:
            if e.code == 429:
                try:
                    wait = float(json.loads(e.read() or b"{}").get("retry_after", 2))
                except ValueError:
                    wait = 2
                time.sleep(min(wait, 30) + 0.5)
                continue
            if e.code in (401, 404):
                die(f"Discord rejected the webhook (HTTP {e.code}). It may have been deleted; "
                    "make a new one and update DISCORD_WEBHOOK_URL.")
            die(f"Discord returned HTTP {e.code}: {e.read()[:300]!r}")
        except urllib.error.URLError as e:
            if attempt < 4:
                time.sleep(2 * (attempt + 1)); continue
            die(f"could not reach Discord: {e}")
    die("gave up after repeated rate limiting")


def cmd_send(a):
    url = secret("DISCORD_WEBHOOK_URL")
    if not url:
        die("DISCORD_WEBHOOK_URL is not set. In Discord: channel settings > Integrations > Webhooks > "
            "New Webhook > Copy Webhook URL, then save it with setx DISCORD_WEBHOOK_URL \"<url>\".")
    if not WEBHOOK.match(url):
        die("DISCORD_WEBHOOK_URL doesn't look like a Discord webhook URL (https://discord.com/api/webhooks/...)")
    path = Path(a.outbox_file)
    body = json.loads(path.read_text(encoding="utf-8"))
    if hashlib.sha256(json.dumps(body["messages"]).encode()).hexdigest()[:12] != body.get("sha"):
        die("this outbox file changed after preview; preview it again before sending")
    if body.get("sent_at"):
        die(f"already sent at {datetime.fromtimestamp(body['sent_at'])}; preview again to send a new copy")
    mentions = {"parse": ["users", "roles", "everyone"]} if body.get("allow_mentions") else {"parse": []}
    for i, m in enumerate(body["messages"], 1):
        post(url, {"content": m, "username": body.get("username") or "cEDH Playgroup",
                   "allowed_mentions": mentions})
        if i < len(body["messages"]):
            time.sleep(1)
    body["sent_at"] = now_unix()
    path.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Posted {len(body['messages'])} message(s) ({body['kind']}) to Discord.")


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("preview"); p.set_defaults(f=cmd_preview)
    p.add_argument("--kind", required=True, choices=["pods", "meta", "results", "availability", "other"])
    p.add_argument("--file", help="draft text (Discord markdown); reads stdin if omitted")
    p.add_argument("--username", default="cEDH Playgroup")
    p.add_argument("--allow-mentions", action="store_true", help="let @mentions in the text actually ping")
    p = sp.add_parser("send"); p.set_defaults(f=cmd_send); p.add_argument("outbox_file")
    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
