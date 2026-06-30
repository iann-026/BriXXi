#!/usr/bin/env python3
"""
daily_pipeline.py

Brizzi Daily — automated daily fetch, classify, merge, publish.
Shared core for BOTH the GitHub Actions runner and the local (X260)
systemd fallback. Idempotent and safe to run from either, or both,
on the same day, with zero coordination between them.

THE RULE (settled, final, no exceptions):
Any post — any format, any content, reply or not — posted between
6:00 and 9:30 Europe/Rome LOCAL time gets kept. Everything else is
discarded silently. No holding tier, no review queue, nothing recorded
for out-of-window posts.

Exit codes:
    0  = success (whether or not new posts were found/published)
    1  = scraping/auth failure (likely blocked, or session expired)
    2  = git push failed after retries
    3  = unexpected/fatal error (e.g. missing posts.json)
"""

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, date, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# --- Configuration -----------------------------------------------------------

POSTS_JSON = Path("posts.json")
BACKUP_RETENTION_DAYS = 30
USER_NAME = "salvatorebrizzi"   # the scraper queries by username, not numeric ID
WINDOW_START_HOUR = 6.0
WINDOW_END_HOUR = 9.5
ROME = ZoneInfo("Europe/Rome")

# RAG export — LOCAL-ONLY (see note in main(): this directory only exists on
# the X260, never on a GitHub Actions runner, and that's used deliberately
# as the on/off switch rather than an extra config flag).
RAG_EXPORT_DIR = Path(os.environ.get("RAG_EXPORT_DIR", "__RAG_Updates__"))
RAG_EXPORT_FILENAME = "brizzi_rag_export.txt"

SCRAPE_LIMIT = 1000              # generous cap; covers even a multi-month gap at ~1 post/day
MAX_RATE_LIMIT_WAIT_SECONDS = 20 * 60   # see note near _parse_rate_limit_wait()

# Explicit, never-ambiguous twscrape session path. Defaults to a plain
# relative filename (matches GitHub Actions, which restores the secret
# into its own cwd each run -- no change needed there). The LOCAL fallback
# overrides this via TWSCRAPE_DB_PATH in its systemd service file, pointed
# at one single stable absolute path -- specifically to stop `twscrape`'s
# own relative-path default from spawning a new accidental empty database
# every time some command happens to run from a different folder (which is
# exactly how Gin ended up with 7+ stray copies on the X260).
TWSCRAPE_DB_PATH = os.environ.get("TWSCRAPE_DB_PATH", "accounts.db")

MONTHS_IT = {
    1: 'gennaio', 2: 'febbraio', 3: 'marzo', 4: 'aprile',
    5: 'maggio', 6: 'giugno', 7: 'luglio', 8: 'agosto',
    9: 'settembre', 10: 'ottobre', 11: 'novembre', 12: 'dicembre',
}

STATUS_ID_PAT = re.compile(r'/status/(\d+)')


# --- De-dup key --------------------------------------------------------------

def status_id_from_url(source_url: str) -> str:
    """Extract the trailing numeric tweet ID from a source_url. This is the
    de-dup key: guaranteed unique by X itself, unlike date/time (which is
    *probably* unique but not logically guaranteed to be)."""
    m = STATUS_ID_PAT.search(source_url)
    if not m:
        raise ValueError(f"Could not extract status ID from: {source_url}")
    return m.group(1)


# --- posts.json I/O + backup -------------------------------------------------

def load_posts(path: Path) -> list:
    if not path.exists():
        raise FileNotFoundError(f"{path} not found — refusing to run without an existing archive.")
    return json.loads(path.read_text(encoding="utf-8"))


def backup_posts(path: Path) -> Path:
    """.bak-YYYY-MM-DD naming — same convention already used by
    apply_reply_filter.py / apply_link_filter.py. Skips silently if today's
    backup already exists (e.g. both runners fired the same day)."""
    today_str = datetime.now().strftime("%Y-%m-%d")
    backup_path = path.with_name(f"{path.stem}.bak-{today_str}{path.suffix}")
    if not backup_path.exists():
        shutil.copy2(path, backup_path)
        print(f"  Backup created: {backup_path.name}")
    else:
        print(f"  Backup already exists, leaving as-is: {backup_path.name}")
    return backup_path


def prune_old_backups(path: Path, retention_days: int = BACKUP_RETENTION_DAYS):
    cutoff = date.today() - timedelta(days=retention_days)
    pattern = re.compile(rf"^{re.escape(path.stem)}\.bak-(\d{{4}}-\d{{2}}-\d{{2}}){re.escape(path.suffix)}$")
    pruned = 0
    for f in path.parent.glob(f"{path.stem}.bak-*{path.suffix}"):
        m = pattern.match(f.name)
        if not m:
            continue
        try:
            backup_date = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            continue
        if backup_date < cutoff:
            f.unlink()
            pruned += 1
    if pruned:
        print(f"  Pruned {pruned} backup(s) older than {retention_days} days.")


# --- Dynamic lookback ---------------------------------------------------------

def newest_known_datetime(posts: list) -> datetime:
    """The dynamic lookback anchor: newest local timestamp already in
    posts.json. Self-correcting — works whether the gap is 1 day or 4 months,
    with no magic number to tune and no silent-gap failure mode."""
    return max(datetime.fromisoformat(p["date"]) for p in posts)


# --- Timezone conversion (same logic as the original bug fix) ---------------

def utc_to_rome_local(utc_dt: datetime) -> datetime:
    """Reinterprets a datetime as a true UTC instant and converts to
    Europe/Rome — zoneinfo handles historical + future DST automatically.
    This MUST be applied before any window check, every time, to avoid
    repeating the original mislabeling bug in the new pipeline."""
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=ZoneInfo("UTC"))
    return utc_dt.astimezone(ROME)


def in_morning_window(local_dt: datetime) -> bool:
    hour_decimal = local_dt.hour + local_dt.minute / 60
    return WINDOW_START_HOUR <= hour_decimal < WINDOW_END_HOUR


def format_display_date(local_dt: datetime) -> str:
    return (f"{local_dt.day:02d} {MONTHS_IT[local_dt.month]} {local_dt.year}"
            f" - {local_dt.hour:02d}:{local_dt.minute:02d}")


# --- Scraping — PLACEHOLDER, needs the real twscrape invocation -------------

# --- t.co link extraction (reused from check_links.py's TCO_PAT) -----------

TCO_PAT = re.compile(r'https?://t\.co/\w+')


def extract_and_strip_links(raw_text: str):
    """Splits a raw tweet body into (text-with-links-removed, [links]),
    matching the existing posts.json schema where 'text' is link-free and
    'links' holds whatever was embedded. Same regex already used elsewhere
    in the project (check_links.py) — not a new pattern."""
    links = TCO_PAT.findall(raw_text)
    text = TCO_PAT.sub('', raw_text).strip()
    return text, links


# --- Scraping: the real twscrape wiring, reusing the proven pattern from ---
# --- Year-by-Year_Scraper_Unfiltered.py (same query style, same rate-limit  -
# --- wait-parsing logic) ----------------------------------------------------

def _parse_rate_limit_wait(err: str) -> float:
    """Same 'Next available at HH:MM:SS' parsing as the bulk scraper."""
    match = re.search(r"Next available at (\d{2}:\d{2}:\d{2})", err)
    wait = 15 * 60
    if match:
        now = datetime.now()
        avail = datetime.strptime(match.group(1), "%H:%M:%S").replace(
            year=now.year, month=now.month, day=now.day)
        wait = (avail - now).total_seconds()
        if wait < 0:
            wait += 86400
    return wait


async def _scrape_new_posts_async(since_utc: datetime) -> list:
    # Imported lazily so the rest of this module stays importable/testable
    # even in environments without twscrape installed (e.g. quick local
    # checks of the classification logic alone).
    from twscrape import API

    api = API(TWSCRAPE_DB_PATH)
    await api.pool.login_all()

    # Fail loudly and immediately if there's no usable account, rather than
    # letting twscrape's own default behavior (log a warning, return None)
    # bubble up as "scraped 0 candidates" -- which looks identical to a
    # genuinely quiet day and would silently mask a dead/expired session.
    info = await api.pool.accounts_info()
    active = [a for a in info if a["active"]]
    if not active:
        details = "; ".join(f"{a['username']}: {a['error_msg']}" for a in info) or "no accounts configured at all"
        raise RuntimeError(f"No active twscrape account(s) -- session likely needs re-login. ({details})")

    # X's `since:` operator works on whole calendar dates, not exact times,
    # and its timezone interpretation isn't something I can verify without
    # live access to X — so rather than try to get that boundary exactly
    # right, we deliberately query ONE DAY EARLIER than the real lookback
    # and let the existing de-dup-by-status-ID safety net silently discard
    # anything we already have. Cheap, and removes an entire class of
    # boundary bugs.
    since_date = since_utc.date() - timedelta(days=1)
    query = f"from:{USER_NAME} since:{since_date.isoformat()}"
    print(f"  Query: {query}")

    raw_posts = []
    while True:
        try:
            async for tweet in api.search(query, limit=SCRAPE_LIMIT):
                raw_text = tweet.rawContent.strip()
                if not raw_text:
                    continue
                text, links = extract_and_strip_links(raw_text)
                raw_posts.append({
                    "created_at_utc": tweet.date.astimezone(timezone.utc),
                    "text": text,
                    "links": links,
                    "source_url": tweet.url,
                })
            break
        except Exception as e:
            err = str(e)
            if "No account available" in err or "rate" in err.lower():
                wait = _parse_rate_limit_wait(err)
                if wait > MAX_RATE_LIMIT_WAIT_SECONDS:
                    # Don't tie up a runner for hours over a rate limit —
                    # bail cleanly; tomorrow's run (dynamic lookback) picks
                    # this up automatically, no data is lost.
                    raise RuntimeError(
                        f"Rate-limited with a long wait ({wait/60:.0f} min) — "
                        f"bailing rather than blocking; next scheduled run will retry."
                    ) from e
                print(f"  Rate limited, waiting {wait/60:.1f} min...")
                await asyncio.sleep(wait)
                continue
            raise

    return raw_posts


def scrape_new_posts(since_utc: datetime) -> list:
    """Sync wrapper — twscrape is async, but the rest of this script (and
    its callers, GitHub Actions / systemd) are simplest kept synchronous."""
    return asyncio.run(_scrape_new_posts_async(since_utc))


# --- Classification: the ONE rule ---------------------------------------------

def classify_and_convert(raw_posts: list, known_ids: set) -> list:
    """De-dup check, then the sole window rule. No other filtering of any
    kind — format/content/reply-status is irrelevant. Returns posts already
    shaped to match the existing posts.json schema."""
    kept = []
    for raw in raw_posts:
        sid = status_id_from_url(raw["source_url"])
        if sid in known_ids:
            continue  # de-dup safety net — already in the archive

        local_dt = utc_to_rome_local(raw["created_at_utc"])
        if not in_morning_window(local_dt):
            continue  # outside 6:00-9:30 Rome local -> discarded, no record kept

        kept.append({
            "date": local_dt.replace(tzinfo=None).isoformat(),
            "display_date": format_display_date(local_dt),
            "text": raw["text"],
            "links": raw.get("links", []),
            "source_url": raw["source_url"],
        })
    return kept


# --- RAG export ----------------------------------------------------------------

def write_rag_export(posts: list, path: Path):
    """Plain-text export using the SAME `--- DD month YYYY - HH:MM ---` /
    body / [url] / % convention already used throughout the project's
    existing files (master.txt and friends) — no new format invented.

    LOCAL-ONLY by design: written straight to Gin's __RAG_Updates__ folder
    on the X260, never committed to the repo. AnythingLLM reads it directly
    off local disk — there's no reason for GitHub or the PWA to know this
    file exists at all."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for p in posts:
            dt = datetime.fromisoformat(p["date"])
            f.write(f"--- {dt.day:02d} {MONTHS_IT[dt.month]} {dt.year} - {dt.hour:02d}:{dt.minute:02d} ---\n")
            f.write(p["text"] + "\n")
            f.write(f"[{p['source_url']}]\n")
            f.write("%\n\n")


# --- Git: commit + push with retry-on-conflict ---------------------------------

def run(cmd: list):
    return subprocess.run(cmd, capture_output=True, text=True)


def git_pull():
    """Sync with whatever the OTHER runner may have already pushed today,
    before computing the lookback anchor or regenerating the local RAG
    export. Failure here isn't fatal — worst case we work from slightly
    stale local state and the de-dup safety net absorbs the overlap."""
    result = run(["git", "pull", "--rebase"])
    if result.returncode != 0:
        print(f"  Warning: git pull failed, continuing with local state.\n    {result.stderr.strip()}")


def git_commit_and_push(paths: list, message: str, max_retries: int = 3) -> bool:
    """Two independent runners might push around the same time. A rejected
    push isn't corrupted data, just 'someone pushed first' — pull, re-merge,
    retry."""
    run(["git", "add"] + [str(p) for p in paths])

    # Structural check instead of parsing git's English message text, which
    # varies by git version/locale ("nothing to commit, working tree clean"
    # vs "nothing added to commit but untracked files present", etc.).
    # `git diff --cached --quiet` returns 0 if nothing is staged.
    nothing_staged = run(["git", "diff", "--cached", "--quiet"]).returncode == 0
    if nothing_staged:
        print("  Nothing new to commit.")
        return True

    commit = run(["git", "commit", "-m", message])
    if commit.returncode != 0:
        print(f"  Commit failed unexpectedly: {commit.stdout}{commit.stderr}")
        return False

    for attempt in range(1, max_retries + 1):
        push = run(["git", "push"])
        if push.returncode == 0:
            print("  Pushed successfully.")
            return True
        print(f"  Push rejected (attempt {attempt}/{max_retries}) — pulling and retrying...")
        run(["git", "pull", "--rebase"])

    print("  Push failed after retries.")
    return False


# --- Main -----------------------------------------------------------------------

def main():
    print(f"=== Brizzi Daily pipeline — {datetime.now().isoformat(timespec='seconds')} ===")

    git_pull()

    try:
        posts = load_posts(POSTS_JSON)
    except FileNotFoundError as e:
        print(f"FATAL: {e}")
        sys.exit(3)

    known_ids = {status_id_from_url(p["source_url"]) for p in posts}
    lookback_dt = newest_known_datetime(posts)
    print(f"Dynamic lookback anchor: newest known post at {lookback_dt.isoformat()} (local)")

    today_rome = datetime.now(ROME).date()
    already_have_today = any(
        datetime.fromisoformat(post["date"]).date() == today_rome for post in posts
    )

    if already_have_today:
        print(f"Already have a post dated today ({today_rome.isoformat()}) — skipping scrape this run.")
        raw_posts = []
    else:
        try:
            raw_posts = scrape_new_posts(since_utc=lookback_dt)
        except NotImplementedError:
            raise
        except Exception as e:
            print(f"FATAL: scraping failed — {e}")
            sys.exit(1)

    new_posts = classify_and_convert(raw_posts, known_ids)
    print(f"Scraped {len(raw_posts)} candidate post(s), {len(new_posts)} fall inside the morning window.")

    if new_posts:
        backup_posts(POSTS_JSON)
        prune_old_backups(POSTS_JSON)
        merged = posts + new_posts
        merged.sort(key=lambda p: p["date"])
        POSTS_JSON.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  posts.json updated: {len(merged)} total posts ({len(new_posts)} new).")
    else:
        merged = posts
        print("Nothing new to publish today.")

    # RAG export — LOCAL-ONLY, deliberately decoupled from "did THIS run find
    # anything new." It just reflects whatever the current state is, every
    # time this runs locally. Matters because on most days GitHub Actions
    # will be the one that actually adds the post (pushed earlier that
    # morning) — without this decoupling, the local RAG file would only ever
    # update on days the LOCAL run itself happened to find something, which
    # defeats the point. RAG_EXPORT_DIR existing at all is what tells us
    # we're on the X260 rather than a GitHub Actions runner — no separate
    # config flag needed.
    if RAG_EXPORT_DIR.exists():
        rag_path = RAG_EXPORT_DIR / RAG_EXPORT_FILENAME
        write_rag_export(merged, rag_path)
        print(f"  RAG export regenerated locally: {rag_path}")
    else:
        print(f"  ({RAG_EXPORT_DIR} not found here — skipping RAG export, expected on GitHub Actions)")

    if not new_posts:
        sys.exit(0)

    ok = git_commit_and_push(
        [POSTS_JSON],
        message=f"Daily update: +{len(new_posts)} post(s)",
    )
    if not ok:
        sys.exit(2)

    print("=== Done ===")


if __name__ == "__main__":
    main()
