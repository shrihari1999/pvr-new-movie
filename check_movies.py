#!/usr/bin/env python3
"""Check PVR Cinemas for new/now-showing movies and send alerts."""

import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

API_BASE = "https://api3.pvrcinemas.com/api/v1/booking/content"
HEADERS = {
    "chain": "PVR",
    "platform": "WEBSITE",
    "country": "INDIA",
    "flow": "PVRINOX",
    "appVersion": "1.0",
    "Content-Type": "application/json",
    "Origin": "https://www.pvrcinemas.com",
    "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:148.0) Gecko/20100101 Firefox/148.0",
}

DATA_DIR = Path("data")
SNAPSHOT_FILE = DATA_DIR / "last_movies.json"
KNOWN_CITIES_FILE = DATA_DIR / "known_cities.json"
KNOWN_LANGUAGES_FILE = DATA_DIR / "known_languages.json"
KNOWN_GENRES_FILE = DATA_DIR / "known_genres.json"
KNOWN_CERTIFICATES_FILE = DATA_DIR / "known_certificates.json"


def api_post(url, headers, body):
    """POST JSON to a URL and return parsed JSON response."""
    data = json.dumps(body).encode()
    req = Request(url, data=data, headers=headers, method="POST")
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def fetch_cities():
    """Fetch all available cities from PVR API."""
    headers = {**HEADERS, "city": ""}
    data = api_post(f"{API_BASE}/city", headers, {"lat": "0.000", "lng": "0.000"})
    return data.get("output", {}).get("ot", [])


def update_known_values(filepath, new_values):
    """Merge new values into a known-values JSON file. Returns True if new entries were added."""
    DATA_DIR.mkdir(exist_ok=True)
    known = set()
    if filepath.exists():
        known = set(json.loads(filepath.read_text()))

    new_values = {v for v in new_values if v}
    if new_values - known:
        known |= new_values
        filepath.write_text(json.dumps(sorted(known), indent=2))
        return True
    return False


def extract_from_movies(movies):
    """Extract all unique languages, genres, and certificates from movie data."""
    languages = set()
    genres = set()
    certificates = set()
    for m in movies:
        for lang in m.get("mfs", []):
            if lang:
                languages.add(lang)
        for genre in m.get("grs", []):
            if genre:
                genres.add(genre)
        ce = m.get("ce", "")
        if ce:
            certificates.add(ce)
    return languages, genres, certificates


def fetch_now_showing(city: str):
    """Fetch now-showing movies for a given city."""
    headers = {**HEADERS, "city": city}
    data = api_post(f"{API_BASE}/nowshowing", headers, {"city": city})
    movies = data.get("output", {}).get("mv", [])
    return movies


def load_snapshot():
    """Load the previous movie snapshot."""
    if SNAPSHOT_FILE.exists():
        return json.loads(SNAPSHOT_FILE.read_text())
    return []


def save_snapshot(movies):
    """Save current movie list as the new snapshot."""
    DATA_DIR.mkdir(exist_ok=True)
    SNAPSHOT_FILE.write_text(json.dumps(movies, indent=2))


def parse_filter(env_var):
    """Parse a comma-separated env var into a set of lowercase values, or None if unset."""
    val = os.environ.get(env_var, "").strip()
    if not val:
        return None
    return {v.strip().lower() for v in val.split(",") if v.strip()}


def filter_movies(movies):
    """Filter movies by configured language, genre, and certificate preferences."""
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


def detect_new_movies(current, previous):
    """Return movies in current that weren't in previous."""
    prev_ids = {m.get("id") for m in previous}
    return [m for m in current if m.get("id") not in prev_ids]


def as_str(value):
    """Normalize a value that may be a string, list, or None into a display string."""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value if v)
    if isinstance(value, str):
        return value
    return ""


def format_movie(movie):
    """Format a movie for the alert message."""
    name = as_str(movie.get("n"))
    # mfs/grs are reliably arrays; otherlanguages/othergenres can be string or array
    languages = as_str(movie.get("mfs")) or as_str(movie.get("otherlanguages"))
    genres = as_str(movie.get("grs")) or as_str(movie.get("othergenres"))
    certificate = as_str(movie.get("ce"))
    duration = as_str(movie.get("mlength"))
    starring = as_str(movie.get("starring"))
    director = as_str(movie.get("director"))
    release = as_str(movie.get("rt"))
    parts = [f"🎬 {name or 'Unknown'}"]
    if certificate:
        parts.append(f"  Certificate: {certificate}")
    if duration:
        parts.append(f"  Duration: {duration}")
    if languages:
        parts.append(f"  Language: {languages}")
    if genres:
        parts.append(f"  Genre: {genres}")
    if director:
        parts.append(f"  Director: {director}")
    if starring:
        parts.append(f"  Cast: {starring}")
    if release:
        parts.append(f"  Status: {release}")
    return "\n".join(parts)


def send_telegram(token: str, chat_id: str, title: str, body: str):
    """Send alert via Telegram bot."""
    text = f"*{title}*\n\n{body}"
    payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}).encode()
    req = Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=15):
        pass


def send_alert(new_movies, city: str):
    """Send alert for new movies via Telegram."""
    if not new_movies:
        return

    title = f"{len(new_movies)} new movie(s) on PVR — {city}"
    body = "\n\n".join(format_movie(m) for m in new_movies)
    print(f"\n{title}\n{body}\n")

    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    telegram_chat = os.environ.get("TELEGRAM_CHAT_ID")
    if telegram_token and telegram_chat:
        send_telegram(telegram_token, telegram_chat, title, body)
        print("Sent Telegram alert")
    else:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set, skipping alert")


def main():
    city = os.environ.get("PVR_CITY", "Chennai")

    # Update known cities knowledge base
    print("Fetching cities...")
    cities = fetch_cities()
    city_names = [c.get("name", "") for c in cities]
    if update_known_values(KNOWN_CITIES_FILE, city_names):
        print(f"Updated known cities ({len(cities)} total)")
    else:
        print(f"No new cities discovered ({len(cities)} total)")

    print(f"\nFetching now-showing movies for {city}...")
    current_movies = fetch_now_showing(city)
    print(f"Found {len(current_movies)} movies currently showing")

    # Update known filters knowledge base
    languages, genres, certificates = extract_from_movies(current_movies)
    updated = []
    if update_known_values(KNOWN_LANGUAGES_FILE, languages):
        updated.append("languages")
    if update_known_values(KNOWN_GENRES_FILE, genres):
        updated.append("genres")
    if update_known_values(KNOWN_CERTIFICATES_FILE, certificates):
        updated.append("certificates")
    if updated:
        print(f"Updated known filters: {', '.join(updated)}")
    else:
        print("No new filter values discovered")

    # Detect new movies, apply filters, and alert
    previous_movies = load_snapshot()
    new_movies = detect_new_movies(current_movies, previous_movies)
    filtered_movies = filter_movies(new_movies)

    if new_movies and len(filtered_movies) < len(new_movies):
        print(f"Detected {len(new_movies)} new movie(s), {len(filtered_movies)} match filters")

    if filtered_movies:
        send_alert(filtered_movies, city)
    else:
        print("No new movies since last check.")

    # Save current state for next run
    save_snapshot(current_movies)


if __name__ == "__main__":
    main()
