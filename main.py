#!/usr/bin/env python3
"""Copper news bot.

Fetches copper/mining news via Google News RSS (publicly accessible from
GitHub Actions), records all seen articles in News_List.md, and sends only
new ones to Telegram as a single combined message with Chinese-translated
subtitles.

Note: mining.com direct RSS and HTML both return 403 from GitHub Actions IPs,
so Google News RSS is used as the primary source.

Runs via GitHub Actions — no local machine required.
"""

import os
import re
import sys
import requests
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator


# ── Constants ──────────────────────────────────────────────────────────────────

NEWS_LIST_FILE = "News_List.md"

# Google News RSS: copper news, filtered to mining.com by <source> tag.
# Direct mining.com access returns 403 from GitHub Actions IPs; Google News is the proxy.
# Note: site: operator is not supported in Google News RSS — filter by source instead.
GOOGLE_NEWS_RSS = (
    "https://news.google.com/rss/search"
    "?q=copper&hl=en-US&gl=US&ceid=US:en"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


# ── Scraping ───────────────────────────────────────────────────────────────────

def scrape_news():
    """Return list of article dicts with keys: url, title, date, subtitle."""
    articles = _fetch_via_google_news()
    print(f"Found {len(articles)} articles total")
    return articles


# ── RSS (primary) ──────────────────────────────────────────────────────────────

def _fetch_via_google_news():
    """Fetch copper/mining news via Google News RSS.

    Google News is publicly accessible from GitHub Actions (unlike mining.com
    which returns 403 for all requests from CI runner IPs).
    Returns list of article dicts with keys: url, title, date, subtitle.
    """
    try:
        resp = requests.get(GOOGLE_NEWS_RSS, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"Google News RSS error: {e}")
        return []

    soup = BeautifulSoup(resp.content, "lxml-xml")
    items = soup.find_all("item")
    if not items:
        print("Google News RSS: no <item> elements found")
        return []

    articles = []
    for item in items:
        title_tag  = item.find("title")
        link_tag   = item.find("link")
        pub_tag    = item.find("pubDate")
        source_tag = item.find("source")
        desc_tag   = item.find("description")

        title  = title_tag.get_text(strip=True)  if title_tag  else ""
        link   = link_tag.get_text(strip=True)   if link_tag   else ""
        pub    = pub_tag.get_text(strip=True)    if pub_tag    else ""
        source = source_tag.get_text(strip=True) if source_tag else ""
        desc   = desc_tag.get_text(strip=True)   if desc_tag   else ""

        if not title or not link:
            continue

        # Filter: keep only articles from mining.com
        if "mining.com" not in source.lower():
            continue

        # Strip source name from end of title (Google appends " - Source Name")
        if source and title.endswith(f" - {source}"):
            title = title[: -(len(source) + 3)].strip()

        # Use description snippet as subtitle if it's meaningful (>30 chars),
        # otherwise omit (source name is already obvious since we only show mining.com)
        desc_clean = BeautifulSoup(desc, "html.parser").get_text(strip=True)
        subtitle = _truncate(desc_clean) if len(desc_clean) > 30 and desc_clean != title else ""

        # Use Google's redirect URL directly — mining.com blocks HEAD requests from
        # CI runner IPs, so redirect resolution would fail. The Google URL works fine
        # in a user's browser.
        articles.append({
            "url":      link,
            "title":    title,
            "date":     _parse_rss_date(pub),
            "subtitle": subtitle,
        })

    print(f"Google News RSS: {len(articles)} articles fetched")
    return articles



def _parse_rss_date(pub_str):
    """Parse an RFC 2822 date string and return 'YYYY-MM-DD' in GMT+8."""
    if not pub_str:
        return ""
    try:
        gmt8 = timezone(timedelta(hours=8))
        dt = parsedate_to_datetime(pub_str).astimezone(gmt8)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return pub_str


def _truncate(text, max_len=200):
    if len(text) <= max_len:
        return text
    return text[:max_len].rsplit(" ", 1)[0] + "…"


# ── Translation ────────────────────────────────────────────────────────────────

def _translate_to_zh(text):
    """Translate text to Simplified Chinese. Returns original on failure.

    Short strings (≤40 chars, likely just a source name) are not translated.
    """
    if not text:
        return ""
    if len(text) <= 40:
        return text  # source names like "mining.com", "Reuters" — no translation
    try:
        return GoogleTranslator(source="auto", target="zh-CN").translate(text)
    except Exception as e:
        print(f"Translation error: {e}")
        return text


# ── News_List.md persistence ───────────────────────────────────────────────────

def _norm_url(url):
    """Normalize a URL for dedup comparison: strip trailing slash."""
    return url.rstrip("/")


def load_seen_urls():
    """Read News_List.md and return a set of all recorded URLs (normalized)."""
    if not os.path.exists(NEWS_LIST_FILE):
        return set()
    seen = set()
    with open(NEWS_LIST_FILE, "r", encoding="utf-8") as f:
        for line in f:
            m = re.search(r"\*\*URL\*\*:\s*(https?://\S+)", line)
            if m:
                seen.add(_norm_url(m.group(1).strip()))
    return seen


def save_news_list(new_items):
    """Append new_items to News_List.md, creating the file if needed."""
    if not os.path.exists(NEWS_LIST_FILE):
        with open(NEWS_LIST_FILE, "w", encoding="utf-8") as f:
            f.write("# Copper News List\n\n")

    timestamp = _now_gmt8().strftime("%Y-%m-%d %H:%M GMT+8")
    with open(NEWS_LIST_FILE, "a", encoding="utf-8") as f:
        for item in new_items:
            f.write("\n---\n\n")
            f.write(f"## {item['title']}\n\n")
            f.write(f"- **Date**: {item['date']}\n")
            f.write(f"- **URL**: {item['url']}\n")
            if item.get("subtitle"):
                f.write(f"- **Subtitle**: {item['subtitle']}\n")
            f.write(f"- **Added**: {timestamp}\n")

    print(f"Saved {len(new_items)} new articles to {NEWS_LIST_FILE}")


def _now_gmt8():
    return datetime.now(timezone(timedelta(hours=8)))


# ── Telegram ───────────────────────────────────────────────────────────────────

def send_telegram(bot_token, channel_id, items):
    """Build one combined message for all items and post it to Telegram."""
    lines = ["📰 <b>铜矿新闻</b>"]

    for i, item in enumerate(items, 1):
        title = _escape_html(item["title"])
        url   = item["url"]
        date  = _escape_html(item.get("date", ""))
        subtitle_zh = _translate_to_zh(item.get("subtitle", ""))

        url_safe = url.replace("&", "&amp;")
        lines.append("")
        lines.append(f'{i}. <b><a href="{url_safe}">{title}</a></b>')
        if date:
            lines.append(f"📅 {date}")
        if subtitle_zh:
            lines.append(_escape_html(subtitle_zh))

    full_text = "\n".join(lines)

    sent_count = 0
    for chunk in _split_message(full_text, 4096):
        _post_message(bot_token, channel_id, chunk)
        sent_count += 1

    return sent_count


def _post_message(bot_token, channel_id, text):
    api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    resp = requests.post(
        api_url,
        json={
            "chat_id":                  channel_id,
            "text":                     text,
            "parse_mode":               "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    data = resp.json()
    if not data.get("ok"):
        # If Telegram rejects the HTML, retry as plain text
        if "parse entities" in str(data).lower() or "can't parse" in str(data).lower():
            print(f"HTML parse error from Telegram, retrying as plain text: {data}")
            plain = re.sub(r"<[^>]+>", "", text)
            resp = requests.post(
                api_url,
                json={
                    "chat_id":                  channel_id,
                    "text":                     plain,
                    "disable_web_page_preview": True,
                },
                timeout=30,
            )
            data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error: {data}")
    return data


def _split_message(text, limit):
    """Split text into chunks of at most `limit` chars, breaking at newlines."""
    if len(text) <= limit:
        return [text]
    chunks, current, current_len = [], [], 0
    for line in text.split("\n"):
        # +1 for the newline character
        if current_len + len(line) + 1 > limit and current:
            chunks.append("\n".join(current))
            current, current_len = [], 0
        current.append(line)
        current_len += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def _escape_html(text):
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;"))


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    bot_token  = os.environ.get("BOT_TOKEN", "")
    channel_id = os.environ.get("CHANNEL_ID", "")

    if not bot_token or not channel_id:
        print("ERROR: BOT_TOKEN and CHANNEL_ID environment variables are required.")
        sys.exit(1)

    # 1. Fetch articles (RSS → HTML fallback)
    all_articles = scrape_news()
    if not all_articles:
        print("No articles found. Exiting.")
        return

    # 2. Identify articles not yet in News_List.md
    seen_urls    = load_seen_urls()
    print(f"Seen URLs in {NEWS_LIST_FILE}: {len(seen_urls)}")
    new_articles = [a for a in all_articles if _norm_url(a["url"]) not in seen_urls]
    print(f"New articles (not in {NEWS_LIST_FILE}): {len(new_articles)}")
    for a in new_articles[:5]:
        print(f"  NEW: {a['url']}")

    if not new_articles:
        print("No new articles to send.")
        return

    # 3. Send all new articles as a single combined Telegram message
    try:
        chunks_sent = send_telegram(bot_token, channel_id, new_articles)
        print(f"Sent {len(new_articles)} articles in {chunks_sent} message(s).")
    except Exception as e:
        print(f"ERROR sending to Telegram: {e}")
        sys.exit(1)

    # 4. Persist new articles to News_List.md
    save_news_list(new_articles)


if __name__ == "__main__":
    main()
