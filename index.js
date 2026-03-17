/**
 * Cloudflare Worker — PVR New Movie Alert.
 * Runs on a cron trigger, checks PVR for new movies, sends Telegram alerts.
 * State stored in Cloudflare KV.
 *
 * KV keys:
 *   last_movies     — JSON array of movie objects from previous run
 *   known_cities    — JSON array of city names
 *   known_languages — JSON array of languages
 *   known_genres    — JSON array of genres
 *   known_certs     — JSON array of certificates
 *
 * Env vars (set in Cloudflare dashboard):
 *   KV                  — KV namespace binding
 *   TELEGRAM_BOT_TOKEN  — Telegram bot token
 *   TELEGRAM_CHAT_ID    — Telegram chat ID
 *   PVR_CITY            — City name (default: Chennai)
 *   PVR_LANGUAGES       — Comma-separated language filter (optional)
 *   PVR_GENRES          — Comma-separated genre filter (optional)
 *   PVR_CERTIFICATES    — Comma-separated certificate filter (optional)
 */

const PVR_BASE = "https://api3.pvrcinemas.com/api/v1/booking/content";

const PVR_HEADERS = {
  chain: "PVR",
  platform: "WEBSITE",
  country: "INDIA",
  flow: "PVRINOX",
  appVersion: "1.0",
  "Content-Type": "application/json",
  Origin: "https://www.pvrcinemas.com",
  "User-Agent":
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:148.0) Gecko/20100101 Firefox/148.0",
};

async function pvrPost(endpoint, body, city = "") {
  const headers = { ...PVR_HEADERS };
  if (city !== undefined) headers.city = city;
  const resp = await fetch(`${PVR_BASE}/${endpoint}`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`PVR ${endpoint}: HTTP ${resp.status}`);
  return resp.json();
}

async function getKV(kv, key) {
  const val = await kv.get(key);
  return val ? JSON.parse(val) : [];
}

async function putKV(kv, key, data) {
  await kv.put(key, JSON.stringify(data));
}

function updateKnownValues(existing, newValues) {
  const set = new Set(existing);
  const added = newValues.filter((v) => v && !set.has(v));
  if (added.length === 0) return { updated: false, merged: existing };
  return { updated: true, merged: [...new Set([...existing, ...added])].sort() };
}

function extractFilters(movies) {
  const languages = new Set();
  const genres = new Set();
  const certs = new Set();
  for (const m of movies) {
    for (const l of m.mfs || []) if (l) languages.add(l);
    for (const g of m.grs || []) if (g) genres.add(g);
    if (m.ce) certs.add(m.ce);
  }
  return {
    languages: [...languages],
    genres: [...genres],
    certs: [...certs],
  };
}

function parseFilter(env, key) {
  const val = (env[key] || "").trim();
  if (!val) return null;
  return new Set(val.split(",").map((v) => v.trim().toLowerCase()).filter(Boolean));
}

function filterMovies(movies, env) {
  const langFilter = parseFilter(env, "PVR_LANGUAGES");
  const genreFilter = parseFilter(env, "PVR_GENRES");
  const certFilter = parseFilter(env, "PVR_CERTIFICATES");

  if (!langFilter && !genreFilter && !certFilter) return movies;

  return movies.filter((m) => {
    const langs = (m.mfs || []).map((v) => v.toLowerCase());
    const genres = (m.grs || []).map((v) => v.toLowerCase());
    const cert = (m.ce || "").toLowerCase();

    if (langFilter && !langs.some((l) => langFilter.has(l))) return false;
    if (genreFilter && !genres.some((g) => genreFilter.has(g))) return false;
    if (certFilter && !certFilter.has(cert)) return false;
    return true;
  });
}

function detectNewMovies(current, previous) {
  const prevIds = new Set(previous.map((m) => m.id));
  return current.filter((m) => !prevIds.has(m.id));
}

function formatMovie(m) {
  const parts = [`🎬 ${m.n || "Unknown"}`];
  if (m.ce) parts.push(`  Certificate: ${m.ce}`);
  if (m.mlength) parts.push(`  Duration: ${m.mlength}`);
  const langs = (m.mfs || []).join(", ");
  if (langs) parts.push(`  Language: ${langs}`);
  const genres = (m.grs || []).join(", ");
  if (genres) parts.push(`  Genre: ${genres}`);
  if (m.director) parts.push(`  Director: ${m.director}`);
  if (m.starring) parts.push(`  Cast: ${m.starring}`);
  if (m.rt) parts.push(`  Status: ${m.rt}`);
  return parts.join("\n");
}

async function sendTelegram(token, chatId, title, body) {
  const text = `*${title}*\n\n${body}`;
  const resp = await fetch(
    `https://api.telegram.org/bot${token}/sendMessage`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chat_id: chatId,
        text,
        parse_mode: "Markdown",
      }),
    }
  );
  if (!resp.ok) {
    const err = await resp.text();
    throw new Error(`Telegram error: ${resp.status} ${err}`);
  }
}

async function checkMovies(env) {
  const city = env.PVR_CITY || "Chennai";
  const logs = [];
  const log = (msg) => { logs.push(msg); console.log(msg); };

  // Fetch and update known cities
  log("Fetching cities...");
  try {
    const cityData = await pvrPost("city", { lat: "0.000", lng: "0.000" }, "");
    const cityNames = (cityData.output?.ot || []).map((c) => c.name).filter(Boolean);
    const knownCities = await getKV(env.KV, "known_cities");
    const { updated, merged } = updateKnownValues(knownCities, cityNames);
    if (updated) {
      await putKV(env.KV, "known_cities", merged);
      log(`Updated known cities (${merged.length} total)`);
    } else {
      log(`No new cities discovered (${knownCities.length} total)`);
    }
  } catch (e) {
    log(`Warning: Could not fetch cities (${e.message}), skipping`);
  }

  // Fetch now-showing movies
  log(`\nFetching now-showing movies for ${city}...`);
  const movieData = await pvrPost("nowshowing", { city }, city);
  const currentMovies = movieData.output?.mv || [];
  log(`Found ${currentMovies.length} movies currently showing`);

  // Update known filters
  const { languages, genres, certs } = extractFilters(currentMovies);
  const updatedFilters = [];

  for (const [key, values] of [
    ["known_languages", languages],
    ["known_genres", genres],
    ["known_certs", certs],
  ]) {
    const known = await getKV(env.KV, key);
    const { updated, merged } = updateKnownValues(known, values);
    if (updated) {
      await putKV(env.KV, key, merged);
      updatedFilters.push(key.replace("known_", ""));
    }
  }
  if (updatedFilters.length) {
    log(`Updated known filters: ${updatedFilters.join(", ")}`);
  } else {
    log("No new filter values discovered");
  }

  // Detect new movies and apply filters
  const previousMovies = await getKV(env.KV, "last_movies");
  const newMovies = detectNewMovies(currentMovies, previousMovies);
  const filtered = filterMovies(newMovies, env);

  if (newMovies.length && filtered.length < newMovies.length) {
    log(`Detected ${newMovies.length} new movie(s), ${filtered.length} match filters`);
  }

  // Send alert
  if (filtered.length) {
    const title = `${filtered.length} new movie(s) on PVR — ${city}`;
    const body = filtered.map(formatMovie).join("\n\n");
    log(`\n${title}\n${body}\n`);

    if (env.TELEGRAM_BOT_TOKEN && env.TELEGRAM_CHAT_ID) {
      await sendTelegram(env.TELEGRAM_BOT_TOKEN, env.TELEGRAM_CHAT_ID, title, body);
      log("Sent Telegram alert");
    } else {
      log("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set, skipping alert");
    }
  } else {
    log("No new movies since last check.");
  }

  // Save snapshot
  await putKV(env.KV, "last_movies", currentMovies);

  return logs.join("\n");
}

export default {
  // Cron trigger — runs on schedule
  async scheduled(event, env, ctx) {
    ctx.waitUntil(checkMovies(env));
  },

  // HTTP trigger — for manual testing
  async fetch(request, env) {
    if (request.method !== "POST" && request.method !== "GET") {
      return new Response("Method not allowed", { status: 405 });
    }

    // Simple API key check for manual triggers
    const apiKey = request.headers.get("x-api-key");
    if (env.API_KEY && (!apiKey || apiKey !== env.API_KEY)) {
      return new Response("Unauthorized", { status: 401 });
    }

    try {
      const result = await checkMovies(env);
      return new Response(result, { headers: { "Content-Type": "text/plain" } });
    } catch (e) {
      return new Response(`Error: ${e.message}`, { status: 500 });
    }
  },
};
