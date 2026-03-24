#!/usr/bin/env python3
"""Copper news bot.

Scrapes https://www.mining.com/commodity/copper/ for news (title, date, subtitle),
records all seen articles in News_List.md, and sends only new ones to Telegram.

Runs via GitHub Actions — no local machine required.
"""

import os
import re
import sys
import requests
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup


# ── Constants ──────────────────────────────────────────────────────────────────

SOURCE_URL = "https://www.mining.com/commodity/copper/"
NEWS_LIST_FILE = "News_List.md"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


# ── Scraping ───────────────────────────────────────────────────────────────────

def scrape_news():
    """Scrape mining.com copper page and return list of article dicts.

    Each dict has: url, title, date, subtitle.
    """
    resp = requests.get(SOURCE_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    articles = []
    seen_urls = set()

    # Strategy 1: find <article> tags (standard HTML5 / WordPress pattern)
    for art in soup.find_all("article"):
        item = _extract_from_container(art)
        if item and item["url"] not in seen_urls:
            seen_urls.add(item["url"])
            articles.append(item)

    # Strategy 2: fallback — look for common card/post div wrappers
    if not articles:
        for cls in ("post", "article", "entry", "card", "item", "news-item",
                    "td_module", "jeg_post"):
            for div in soup.find_all("div", class_=re.compile(cls, re.I)):
                item = _extract_from_container(div)
                if item and item["url"] not in seen_urls:
                    seen_urls.add(item["url"])
                    articles.append(item)
            if articles:
                break

    # Strategy 3: last resort — find all h2/h3 links and grab nearby date/excerpt
    if not articles:
        for heading in soup.find_all(["h2", "h3"]):
            link = heading.find("a", href=True)
            if not link:
                continue
            href = _normalize_url(link.get("href", ""))
            if not href or "mining.com" not in href:
                continue
            title = heading.get_text(strip=True)
            if len(title) < 10:
                continue
            if href in seen_urls:
                continue

            # Walk up to find date and subtitle in the surrounding container
            container = heading.parent
            for _ in range(5):
                if container is None:
                    break
                date = _find_date(container)
                subtitle = _find_subtitle(container, heading)
                if date or subtitle:
                    break
                container = container.parent

            seen_urls.add(href)
            articles.append({
                "url": href,
                "title": title,
                "date": date or "",
                "subtitle": subtitle or "",
            })

    print(f"Scraped {len(articles)} articles from {SOURCE_URL}")
    return articles


def _extract_from_container(container):
    """Try to extract title, url, date, subtitle from a container element."""
    # Title + URL
    heading = container.find(["h1", "h2", "h3", "h4"])
    if not heading:
        return None
    link = heading.find("a", href=True) or container.find("a", href=True)
    if not link:
        return None
    href = _normalize_url(link.get("href", ""))
    if not href or "mining.com" not in href:
        return None
    title = heading.get_text(strip=True)
    if len(title) < 10:
        return None

    date = _find_date(container)
    subtitle = _find_subtitle(container, heading)

    return {
        "url": href,
        "title": title,
        "date": date or "",
        "subtitle": subtitle or "",
    }


def _normalize_url(href):
    """Ensure URL is absolute."""
    href = href.strip()
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return "https://www.mining.com" + href
    return href


def _find_date(container):
    """Extract a date string from a container element."""
    # <time datetime="..."> is the most reliable
    time_tag = container.find("time")
    if time_tag:
        dt_attr = time_tag.get("datetime", "")
        text = time_tag.get_text(strip=True)
        return dt_attr[:10] if dt_attr else text

    # Look for elements with date-like class names
    for cls in ("date", "time", "meta", "published", "entry-date", "post-date"):
        el = container.find(class_=re.compile(cls, re.I))
        if el:
            txt = el.get_text(strip=True)
            if txt:
                return txt

    # Try regex on the container text
    text = container.get_text(" ", strip=True)
    # ISO date pattern
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if m:
        return m.group(1)
    # "Month DD, YYYY"
    m = re.search(
        r"\b(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\s+\d{1,2},?\s+\d{4}\b",
        text,
    )
    if m:
        return m.group(0)

    return ""


def _find_subtitle(container, heading_elem):
    """Extract a subtitle/excerpt from a container element, skipping the heading."""
    # Look for explicit excerpt/description elements
    for cls in ("excerpt", "entry-summary", "entry-content", "description",
                "summary", "teaser", "intro", "post-excerpt", "td-excerpt",
                "jeg_post_excerpt"):
        el = container.find(class_=re.compile(cls, re.I))
        if el:
            txt = el.get_text(strip=True)
            if txt:
                return _truncate(txt)

    # Find the first <p> that is NOT inside the heading and has real content
    for p in container.find_all("p"):
        # Skip if p is an ancestor or descendant of the heading
        if heading_elem in p.parents or p in heading_elem.parents:
            continue
        txt = p.get_text(strip=True)
        if len(txt) > 20:
            return _truncate(txt)

    return ""


def _truncate(text, max_len=200):
    """Truncate text to max_len characters, ending at a word boundary."""
    if len(text) <= max_len:
        return text
    truncated = text[:max_len].rsplit(" ", 1)[0]
    return truncated + "…"


# ── News_List.md persistence ───────────────────────────────────────────────────

def load_seen_urls():
    """Read News_List.md and return a set of all URLs recorded there."""
    if not os.path.exists(NEWS_LIST_FILE):
        return set()
    seen = set()
    with open(NEWS_LIST_FILE, "r", encoding="utf-8") as f:
        for line in f:
            m = re.search(r"\*\*URL\*\*:\s*(https?://\S+)", line)
            if m:
                seen.add(m.group(1).strip())
    return seen


def save_news_list(new_items):
    """Append new_items to News_List.md (create file if it doesn't exist)."""
    # Initialise file with header if it doesn't exist
    if not os.path.exists(NEWS_LIST_FILE):
        with open(NEWS_LIST_FILE, "w", encoding="utf-8") as f:
            f.write("# Copper News List\n\n")

    timestamp = _now_gmt8().strftime("%Y-%m-%d %H:%M GMT+8")

    with open(NEWS_LIST_FILE, "a", encoding="utf-8") as f:
        for item in new_items:
            f.write(f"\n---\n\n")
            f.write(f"## {item['title']}\n\n")
            f.write(f"- **Date**: {item['date']}\n")
            f.write(f"- **URL**: {item['url']}\n")
            if item.get("subtitle"):
                f.write(f"- **Subtitle**: {item['subtitle']}\n")
            f.write(f"- **Added**: {timestamp}\n")

    print(f"Saved {len(new_items)} new articles to {NEWS_LIST_FILE}")


def _now_gmt8():
    gmt8 = timezone(timedelta(hours=8))
    return datetime.now(gmt8)


# ── Telegram ───────────────────────────────────────────────────────────────────

def send_telegram(bot_token, channel_id, item):
    """Send a single news article to the Telegram channel."""
    title = _escape_html(item["title"])
    url = item["url"]
    date = _escape_html(item.get("date", ""))
    subtitle = _escape_html(item.get("subtitle", ""))

    lines = [f'📰 <b><a href="{url}">{title}</a></b>']
    if date:
        lines.append(f"📅 {date}")
    if subtitle:
        lines.append(f"\n{subtitle}")
    lines.append(f'\n<a href="{url}">Read more →</a>')

    text = "\n".join(lines)

    api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    resp = requests.post(
        api_url,
        json={
            "chat_id": channel_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        },
        timeout=30,
    )
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data}")
    return data


def _escape_html(text):
    """Escape special HTML characters for Telegram HTML parse mode."""
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
    )


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    bot_token = os.environ.get("BOT_TOKEN", "")
    channel_id = os.environ.get("CHANNEL_ID", "")

    if not bot_token or not channel_id:
        print("ERROR: BOT_TOKEN and CHANNEL_ID environment variables are required.")
        sys.exit(1)

    # 1. Scrape current articles from the page
    all_articles = scrape_news()
    if not all_articles:
        print("No articles found on the page. Exiting.")
        return

    # 2. Find articles not yet recorded in News_List.md
    seen_urls = load_seen_urls()
    new_articles = [a for a in all_articles if a["url"] not in seen_urls]
    print(f"New articles (not in {NEWS_LIST_FILE}): {len(new_articles)}")

    if not new_articles:
        print("No new articles to send.")
        return

    # 3. Send each new article to Telegram
    sent = 0
    for item in new_articles:
        try:
            send_telegram(bot_token, channel_id, item)
            print(f"  Sent: {item['title'][:60]}")
            sent += 1
        except Exception as e:
            print(f"  ERROR sending '{item['title'][:60]}': {e}")

    print(f"Sent {sent}/{len(new_articles)} articles to Telegram.")

    # 4. Persist new articles to News_List.md
    save_news_list(new_articles)


if __name__ == "__main__":
    main()
