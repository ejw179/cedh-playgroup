"""Shared helpers: data folder, JSON load/save, commander name matching."""
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

# Windows consoles default to cp1252; commander names and tables need UTF-8.
for _s in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

ATTRIBUTION ="Tournament data from TopDeck.gg (https://topdeck.gg)"

DEFAULTS = {
    "roster.json": {"players": [], "gauntlet_decks": []},
    "games.json": {"games": []},
    "notes.json": {"notes": []},
    "availability.json": {"sessions": {}},
    "cache/tournaments.json": {"tournaments": {}, "synced": []},
    "config.json": {"home": None},
    "pods.json": {"sessions": {}},
}


def data_dir() -> Path:
    """Where the playgroup's files live. Override with CEDH_PLAYGROUP_DIR."""
    d = Path(os.environ.get("CEDH_PLAYGROUP_DIR") or Path.home() / "cedh-playgroup")
    (d / "cache").mkdir(parents=True, exist_ok=True)
    return d


def secret(name: str) -> str:
    """An env var, or on Windows the user's saved variable, so a fresh
    `setx NAME value` works without restarting the app that launched us."""
    val = os.environ.get(name, "").strip()
    if val or sys.platform != "win32":
        return val
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return str(winreg.QueryValueEx(k, name)[0]).strip()
    except OSError:
        return ""


def load(name: str) -> dict:
    p = data_dir() / name
    if not p.exists():
        return json.loads(json.dumps(DEFAULTS[name]))
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save(name: str, obj: dict) -> None:
    p = data_dir() / name
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, p)


def today() -> str:
    return date.today().isoformat()


def now_unix() -> int:
    return int(time.time())


def fmt_date(unix: float) -> str:
    return datetime.fromtimestamp(unix, tz=timezone.utc).date().isoformat()


def zip_location(zipcode: str, country: str = "us") -> dict:
    """Lat/lng for a postal code via the free Zippopotam.us API (no key; only
    the zip is sent). Cached in <data>/zips.json so each zip is looked up once."""
    zipcode = zipcode.strip()
    cache_path = data_dir() / "zips.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    key = f"{country}:{zipcode}"
    if key in cache:
        return cache[key]
    url = f"https://api.zippopotam.us/{urllib.parse.quote(country)}/{urllib.parse.quote(zipcode)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "cedh-playgroup-skill"})
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        die(f"zip {zipcode} not found ({e.code})" if e.code == 404 else f"zip lookup failed: HTTP {e.code}")
    except urllib.error.URLError as e:
        die(f"could not reach the zip lookup service: {e}")
    place = d["places"][0]
    loc = {"zip": zipcode, "lat": float(place["latitude"]), "lng": float(place["longitude"]),
           "place": f"{place['place name']}, {place.get('state abbreviation') or place.get('state', '')}"}
    cache[key] = loc
    cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    return loc


def miles_between(lat1, lng1, lat2, lng2) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def topdeck_id(raw: str) -> str:
    """Accept a bare ID, an @handle, or a profile link like https://topdeck.gg/profile/<id or %40handle>."""
    raw = raw.strip().rstrip("/")
    m = re.search(r"topdeck\.gg/(?:profile|player)/([^/?#]+)", raw)
    return urllib.parse.unquote(m.group(1) if m else raw)


def resolve_profile(raw: str) -> dict:
    """Turn a profile link, @handle or ID into the player ID tournament standings use.

    Handle links (topdeck.gg/profile/@name) don't contain the ID, but every
    profile page carries it in <meta name="player-id">. Returns
    {"id", "handle", "display_name", "resolved"}."""
    key = topdeck_id(raw)
    handle = key if key.startswith("@") else ""
    url = "https://topdeck.gg/profile/" + urllib.parse.quote(key)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 cedh-playgroup-skill"})
        with urllib.request.urlopen(req, timeout=30) as r:
            html = r.read().decode("utf-8", "replace")
    except Exception as e:  # offline, or TopDeck down
        print(f"  could not open {url} ({e}); storing '{key}' as given", file=sys.stderr)
        return {"id": key, "handle": handle, "display_name": "", "resolved": False}
    m = re.search(r'<meta name="player-id" content="([^"]+)"', html) or \
        re.search(r'PROFILE_PLAYER_ID\s*=\s*"([^"]+)"', html)
    t = re.search(r"<title>(.*?) - Profile", html)
    if not m:
        print(f"  no player ID found on {url}; storing '{key}' as given", file=sys.stderr)
        return {"id": key, "handle": handle, "display_name": t.group(1) if t else "", "resolved": False}
    return {"id": m.group(1), "handle": handle, "display_name": t.group(1) if t else "", "resolved": True}


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9/ ]", "", s.lower()).strip()


def canonical_leader(a: str, b: str | None = None) -> str:
    """TopDeck writes partner pairs alphabetically as 'A / B'."""
    parts = [a] + ([b] if b else [])
    if b is None and "/" in a:
        parts = [p.strip() for p in a.split("/")]
    parts = [p.strip() for p in parts if p and p.strip()]
    return " / ".join(sorted(parts, key=str.lower))


_LEADERS = None


def known_leaders() -> dict[str, int]:
    """Every commander we know, with how often it's been played (roster decks count high)."""
    global _LEADERS
    if _LEADERS is None:
        counts: dict[str, int] = {}
        cache = load("cache/tournaments.json")
        for t in cache["tournaments"].values():
            for s in t.get("standings", []):
                if s.get("leader"):
                    counts[s["leader"]] = counts.get(s["leader"], 0) + 1
        roster = load("roster.json")
        for p in roster["players"]:
            for d in p.get("decks", []) + p.get("practice_decks", []):
                counts[d["commander"]] = counts.get(d["commander"], 0) + 10_000
        for d in roster.get("gauntlet_decks", []):
            counts[d["commander"]] = counts.get(d["commander"], 0) + 10_000
        _LEADERS = counts
    return _LEADERS


def match_commander(text: str, prefer: list[str] | None = None) -> tuple[str, list[str]]:
    """Resolve a typed commander name to TopDeck's spelling.

    `prefer` (e.g. that player's own decks) is tried first. Among several
    matches the most-played wins, so "sisay" means Sisay, Weatherlight
    Captain, not Captain Sisay. Returns (best, alternatives); unknown names
    come back canonicalised so new commanders can still be recorded."""
    want = norm(canonical_leader(text))
    tokens = [t for t in re.split(r"[ /,]+", want) if len(t) > 2]

    def hits_in(names):
        exact = [l for l in names if norm(l) == want]
        if exact:
            return exact
        return [l for l in names if tokens and all(t in norm(l) for t in tokens)]

    if prefer:
        mine = hits_in(prefer)
        if len(mine) == 1:
            return mine[0], []
    counts = known_leaders()
    hits = hits_in(list(counts))
    if not hits:
        return canonical_leader(text), []
    hits.sort(key=lambda l: (-counts[l], len(l)))
    return hits[0], hits[1:6]


def find_player(roster: dict, name: str) -> dict | None:
    n = name.strip().lower()
    for p in roster["players"]:
        if n in (p["name"].lower(), p.get("topdeck_id", "").lower(), p.get("topdeck_handle", "").lower()):
            return p
    return None


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)
