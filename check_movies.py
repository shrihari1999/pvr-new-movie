#!/usr/bin/env python3
"""Check PVR Cinemas for new/now-showing movies and send alerts via Telegram."""

import json
import os
import sys
import traceback
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import quote

PVR_BASE = "https://api3.pvrcinemas.com/api/v1/booking/content"
DATA_DIR = Path("data")
SNAPSHOT_FILE = DATA_DIR / "last_movies.json"
ERROR_MARKER = Path(".error_alerted")


class PVRError(RuntimeError):
    """Raised when the PVR API or the proxy returns something unusable."""


def pvr_post(endpoint, body, city="Chennai"):
    """POST to PVR API via scrape.do proxy."""
    token = os.environ.get("SCRAPE_DO_TOKEN", "")
    if not token:
        raise PVRError("SCRAPE_DO_TOKEN is not set")

    target = quote(f"{PVR_BASE}/{endpoint}", safe="")
    url = f"http://api.scrape.do/?url={target}&token={token}&transparentResponse=true&extraHeaders=true"

    headers = {
        "sd-Host": "api3.pvrcinemas.com",
        "sd-Accept": "application/json",
        "sd-Accept-Language": "en-US,en;q=0.9",
        "sd-Content-Type": "application/json",
        "sd-chain": "PVR",
        "sd-city": city,
        "sd-appVersion": "1.0",
        "sd-platform": "WEBSITE",
        "sd-country": "INDIA",
        "sd-flow": "PVRINOX",
        "sd-Origin": "https://www.pvrcinemas.com",
        "sd-Referer": "https://www.pvrcinemas.com/",
        "Content-Type": "application/json",
    }

    data = json.dumps(body).encode()
    req = Request(url, data=data, headers=headers, method="POST")

    try:
        with urlopen(req, timeout=30) as resp:
            raw = resp.read()
    except HTTPError as e:
        snippet = e.read()[:300].decode("utf-8", "replace").strip()
        raise PVRError(f"proxy returned HTTP {e.code} for /{endpoint}: {snippet}") from None
    except URLError as e:
        raise PVRError(f"could not reach proxy for /{endpoint}: {e.reason}") from None

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        snippet = raw[:300].decode("utf-8", "replace").strip()
        raise PVRError(f"non-JSON response for /{endpoint}: {snippet}") from None

    if not isinstance(payload, dict) or payload.get("result") != "success":
        snippet = json.dumps(payload)[:300]
        raise PVRError(f"unexpected payload for /{endpoint}: {snippet}")

    return payload


def fetch_now_showing(city):
    """Fetch the now-showing list, refusing to accept an empty result as valid."""
    data = pvr_post("nowshowing", {"city": city}, city)
    movies = data.get("output", {}).get("mv") or []
    if not movies:
        raise PVRError(
            f"API reported success but returned no movies for {city}. "
            "Refusing to treat this as 'everything was removed'."
        )
    return movies


def load_snapshot():
    """Return the previous movie list, or None if there is no usable baseline."""
    if not SNAPSHOT_FILE.exists():
        return None
    try:
        data = json.loads(SNAPSHOT_FILE.read_text())
    except json.JSONDecodeError:
        print("Snapshot file is corrupt, treating as no baseline")
        return None
    if not isinstance(data, list) or not data:
        return None
    return data


def save_snapshot(movies):
    DATA_DIR.mkdir(exist_ok=True)
    SNAPSHOT_FILE.write_text(json.dumps(movies, indent=2))


def detect_new_movies(current, previous):
    prev_ids = {m.get("id") for m in previous}
    return [m for m in current if m.get("id") not in prev_ids]


def detect_removed_movies(current, previous):
    curr_ids = {m.get("id") for m in current}
    return [m for m in previous if m.get("id") not in curr_ids]


def parse_filter(env_var):
    val = os.environ.get(env_var, "").strip()
    if not val:
        return None
    return {v.strip().lower() for v in val.split(",") if v.strip()}


def filter_movies(movies):
    lang_filter = parse_filter("PVR_LANGUAGES")
    genre_filter = parse_filter("PVR_GENRES")
    cert_filter = parse_filter("PVR_CERTIFICATES")

    if not any([lang_filter, genre_filter, cert_filter]):
        return movies

    filtered = []
    for m in movies:
        langs = {v.lower() for v in m.get("mfs", []) if v}
        genres = {v.lower() for v in m.get("grs", []) if v}
        cert = m.get("ce", "").lower()

        if lang_filter and not (langs & lang_filter):
            continue
        if genre_filter and not (genres & genre_filter):
            continue
        if cert_filter and cert not in cert_filter:
            continue
        filtered.append(m)
    return filtered


def booking_url(m, city):
    name = m.get("n")
    mid = m.get("id")
    if not name or not mid:
        return None
    return f"https://www.pvrcinemas.com/moviesessions/-{quote(city)}/{quote(name)}/{mid}"


def format_movie(m, city):
    name = m.get("n") or "Unknown"
    url = booking_url(m, city)
    title = f"[{name}]({url})" if url else name
    parts = [f"🎬 *{title}*"]
    if m.get("ce"):
        parts.append(f"*Certificate:* {m['ce']}")
    if m.get("mlength"):
        parts.append(f"*Duration:* {m['mlength']}")
    langs = ", ".join(m.get("mfs", []))
    if langs:
        parts.append(f"*Language:* {langs}")
    genres = ", ".join(m.get("grs", []))
    if genres:
        parts.append(f"*Genre:* {genres}")
    if m.get("director"):
        parts.append(f"*Director:* {m['director']}")
    if m.get("starring"):
        parts.append(f"*Cast:* {m['starring']}")
    if m.get("rt"):
        parts.append(f"*Status:* {m['rt']}")
    return "\n".join(parts)


def telegram_send(text, markdown=True):
    """Send one Telegram message. Returns False if credentials are missing."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set, skipping alert")
        return False

    body = {"chat_id": chat_id, "text": text}
    if markdown:
        body["parse_mode"] = "Markdown"

    req = Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=15):
        pass
    return True


def send_telegram(title, movies, city):
    # Split into chunks to respect Telegram's 4096 char limit
    max_len = 4000
    current = f"*{title}*\n"
    chunks = []

    for m in movies:
        entry = "\n" + format_movie(m, city) + "\n"
        if len(current) + len(entry) > max_len:
            chunks.append(current)
            current = f"*{title} (contd.)*\n"
        current += entry
    if current:
        chunks.append(current)

    for chunk in chunks:
        if not telegram_send(chunk):
            return
    print("Sent Telegram alert")


def send_error_alert(message):
    """Alert on failure. Never raises — a broken alert must not mask the real error."""
    text = f"⚠️ PVR movie checker failed\n\n{message}"
    repo = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if repo and run_id:
        text += f"\n\nLogs: https://github.com/{repo}/actions/runs/{run_id}"

    try:
        # Plain text: error bodies routinely contain characters that break Markdown parsing.
        if telegram_send(text, markdown=False):
            ERROR_MARKER.write_text("sent")
            print("Sent Telegram failure alert")
    except Exception as exc:
        print(f"Could not send failure alert: {exc}")


def run():
    city = os.environ.get("PVR_CITY", "Chennai")

    print(f"Fetching now-showing movies for {city}...")
    current_movies = fetch_now_showing(city)
    print(f"Found {len(current_movies)} movies currently showing")

    previous_movies = load_snapshot()

    if previous_movies is None:
        print("No usable baseline — seeding snapshot instead of alerting on the whole lineup.")
        save_snapshot(current_movies)
        telegram_send(
            f"*PVR movie checker — baseline set for {city}*\n"
            f"Now tracking {len(current_movies)} movies. "
            "Alerts resume from the next change onward."
        )
        return

    new_movies = detect_new_movies(current_movies, previous_movies)
    removed_movies = detect_removed_movies(current_movies, previous_movies)
    filtered_new = filter_movies(new_movies)
    filtered_removed = filter_movies(removed_movies)

    if new_movies and len(filtered_new) < len(new_movies):
        print(f"Detected {len(new_movies)} new movie(s), {len(filtered_new)} match filters")

    if filtered_new:
        title = f"{len(filtered_new)} new movie(s) on PVR — {city}"
        print(f"\n{title}")
        for m in filtered_new:
            print(format_movie(m, city))
            print()
        send_telegram(title, filtered_new, city)

    if filtered_removed:
        title = f"{len(filtered_removed)} movie(s) removed from PVR — {city}"
        print(f"\n{title}")
        for m in filtered_removed:
            print(format_movie(m, city))
            print()
        send_telegram(title, filtered_removed, city)

    if not filtered_new and not filtered_removed:
        print("No changes since last check.")

    save_snapshot(current_movies)


def main():
    try:
        run()
    except PVRError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        send_error_alert(str(exc))
        sys.exit(1)
    except Exception:
        traceback.print_exc()
        send_error_alert(traceback.format_exc(limit=3).strip())
        sys.exit(1)


if __name__ == "__main__":
    main()
