#!/usr/bin/env python3
"""Check PVR Cinemas for new/now-showing movies and send alerts via Telegram."""

import json
import os
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import quote

PVR_BASE = "https://api3.pvrcinemas.com/api/v1/booking/content"
DATA_DIR = Path("data")
SNAPSHOT_FILE = DATA_DIR / "last_movies.json"


def pvr_post(endpoint, body, city="Chennai"):
    """POST to PVR API via scrape.do proxy."""
    token = os.environ.get("SCRAPE_DO_TOKEN", "")
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
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def load_snapshot():
    if SNAPSHOT_FILE.exists():
        return json.loads(SNAPSHOT_FILE.read_text())
    return []


def save_snapshot(movies):
    DATA_DIR.mkdir(exist_ok=True)
    SNAPSHOT_FILE.write_text(json.dumps(movies, indent=2))


def detect_new_movies(current, previous):
    prev_ids = {m.get("id") for m in previous}
    return [m for m in current if m.get("id") not in prev_ids]


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


def format_movie(m):
    name = m.get("n") or "Unknown"
    parts = [f"🎬 *{name}*"]
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


def send_telegram(title, movies):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set, skipping alert")
        return

    # Split into chunks to respect Telegram's 4096 char limit
    max_len = 4000
    current = f"*{title}*\n"
    chunks = []

    for m in movies:
        entry = "\n" + format_movie(m) + "\n"
        if len(current) + len(entry) > max_len:
            chunks.append(current)
            current = f"*{title} (contd.)*\n"
        current += entry
    if current:
        chunks.append(current)

    for chunk in chunks:
        payload = json.dumps({"chat_id": chat_id, "text": chunk, "parse_mode": "Markdown"}).encode()
        req = Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=15):
            pass
    print("Sent Telegram alert")


def main():
    city = os.environ.get("PVR_CITY", "Chennai")

    print(f"Fetching now-showing movies for {city}...")
    data = pvr_post("nowshowing", {"city": city}, city)
    current_movies = data.get("output", {}).get("mv", [])
    print(f"Found {len(current_movies)} movies currently showing")

    previous_movies = load_snapshot()
    new_movies = detect_new_movies(current_movies, previous_movies)
    filtered = filter_movies(new_movies)

    if new_movies and len(filtered) < len(new_movies):
        print(f"Detected {len(new_movies)} new movie(s), {len(filtered)} match filters")

    if filtered:
        title = f"{len(filtered)} new movie(s) on PVR — {city}"
        print(f"\n{title}")
        for m in filtered:
            print(format_movie(m))
            print()
        send_telegram(title, filtered)
    else:
        print("No new movies since last check.")

    save_snapshot(current_movies)


if __name__ == "__main__":
    main()
