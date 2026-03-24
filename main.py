#!/usr/bin/env python3
"""Copper news bot.

Scrapes https://www.mining.com/commodity/copper/ for news (title, date, subtitle),
records all seen articles in News_List.md, and sends only new ones to Telegram.

Primary source: RSS feed (reliable, structured).
Fallback: HTML scraping of the commodity page.

Runs via GitHub Actions — no local machine required.
"""

import os
import re
import sys
import requests
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from bs4 import BeautifulSoup


# ── Constants ──────────────────────────────────────────────────────────────────

SOURCE_URL = "https://www.mining.com/commodity/copper/"
NEWS_LIST_FILE = "News_List.md"

RSS_CANDIDATES = [
    "https://www.mining.com/commodity/copper/feed/",
    "https://www.mining.com/feed/?category=copper",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


# ── Scraping ───────────────────────────────────────────────────────────────────

def scrape_news():
    """Return list of article dicts with keys: url, title, date, subtitle.

    Tries RSS feeds first (more reliable). Falls back to HTML scraping.
    """
    articles = _fetch_via_rss()
    if articles is None:
        print("RSS unavailable, falling back to HTML scraping...")
        articles = _fetch_via_html()
    print(f"Found {len(articles)} articles total")
    return articles


# ── RSS (primary) ──────────────────────────────────────────────────────────────

def _fetch_via_rss():
    """Try RSS feeds. Returns list of article dicts, or None if all feeds fail."""
    for rss_url in RSS_CANDIDATES:
        try:
            resp = requests.get(rss_url, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                print(f"RSS {rss_url}: HTTP {resp.status_code}, skipping")
                continue

            soup = BeautifulSoup(resp.content, "lxml-xml")
            items = soup.find_all("item")
            if not items:
                print(f"RSS {rss_url}: no <item> elements found")
                continue

            articles = []
            for item in items:
                title_tag = item.find("title")
                link_tag  = item.find("link")
                pub_tag   = item.find("pubDate")
                desc_tag  = item.find("description")

                title = title_tag.get_text(strip=True) if title_tag else ""
                link  = link_tag.get_text(strip=True)  if link_tag  else ""
                pub   = pub_tag.get_text(strip=True)   if pub_tag   else ""
                desc  = desc_tag.get_text(strip=True)  if desc_tag  else ""

                if not title or not link:
                    continue

                # description in RSS is often HTML-encoded — strip tags
                desc_clean = BeautifulSoup(desc, "html.parser").get_text(strip=True)

                articles.append({
                    "url":      link,
                    "title":    title,
                    "date":     _parse_rss_date(pub),
                    "subtitle": _truncate(desc_clean),
                })

            if articles:
                print(f"RSS OK ({rss_url}): {len(articles)} articles")
                return articles

        except Exception as e:
            print(f"RSS {rss_url} error: {e}")

    return None


def _parse_rss_date(pub_str):
    """Parse an RFC 2822 date string and return 'YYYY-MM-DD' in GMT+8."""
    if not pub_str:
        return ""
    try:
        gmt8 = timezone(timedelta(hours=8))
        dt = parsedate_to_datetime(pub_str).astimezone(gmt8)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return pub_str  # return raw string if parsing fails


# ── HTML scraping (fallback) ───────────────────────────────────────────────────

def _fetch_via_html():
    """Scrape the copper commodity page. Returns list of article dicts."""
    try:
        resp = requests.get(SOURCE_URL, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"HTML fetch error: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    articles = []
    seen_urls = set()

    # Strategy 1: <article> tags (standard HTML5 / WordPress pattern)
    for art in soup.find_all("article"):
        item = _extract_from_container(art)
        if item and item["url"] not in seen_urls:
            seen_urls.add(item["url"])
            articles.append(item)

    # Strategy 2: common card/post div wrappers
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

    # Strategy 3: h2/h3 headings with nearby date/excerpt
    if not articles:
        for heading in soup.find_all(["h2", "h3"]):
            link = heading.find("a", href=True)
            if not link:
                continue
            href = _normalize_url(link.get("href", ""))
            if not href or "mining.com" not in href:
                continue
            title = heading.get_text(strip=True)
            if len(title) < 10 or href in seen_urls:
                continue

            date, subtitle = "", ""
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
            articles.append({"url": href, "title": title,
                              "date": date, "subtitle": subtitle})

    print(f"HTML scrape: {len(articles)} articles")
    return articles


def _extract_from_container(container):
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
    return {
        "url":      href,
        "title":    title,
        "date":     _find_date(container),
        "subtitle": _find_subtitle(container, heading),
    }


def _normalize_url(href):
    href = href.strip()
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return "https://www.mining.com" + href
    return href


def _find_date(container):
    time_tag = container.find("time")
    if time_tag:
        dt_attr = time_tag.get("datetime", "")
        return dt_attr[:10] if dt_attr else time_tag.get_text(strip=True)
    for cls in ("date", "time", "meta", "published", "entry-date", "post-date"):
        el = container.find(class_=re.compile(cls, re.I))
        if el:
            txt = el.get_text(strip=True)
            if txt:
                return txt
    text = container.get_text(" ", strip=True)
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if m:
        return m.group(1)
    m = re.search(
        r"\b(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\s+\d{1,2},?\s+\d{4}\b", text)
    if m:
        return m.group(0)
    return ""


def _find_subtitle(container, heading_elem):
    for cls in ("excerpt", "entry-summary", "entry-content", "description",
                "summary", "teaser", "intro", "post-excerpt", "td-excerpt",
                "jeg_post_excerpt"):
        el = container.find(class_=re.compile(cls, re.I))
        if el:
            txt = el.get_text(strip=True)
            if txt:
                return _truncate(txt)
    for p in container.find_all("p"):
        if heading_elem in p.parents or p in heading_elem.parents:
            continue
        txt = p.get_text(strip=True)
        if len(txt) > 20:
            return _truncate(txt)
    return ""


def _truncate(text, max_len=200):
    if len(text) <= max_len:
        return text
    return text[:max_len].rsplit(" ", 1)[0] + "…"


# ── News_List.md persistence ───────────────────────────────────────────────────

def load_seen_urls():
    """Read News_List.md and return a set of all recorded URLs."""
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

def send_telegram(bot_token, channel_id, item):
    """Send a single news article to the Telegram channel."""
    title    = _escape_html(item["title"])
    url      = item["url"]
    date     = _escape_html(item.get("date", ""))
    subtitle = _escape_html(item.get("subtitle", ""))

    lines = [f'📰 <b><a href="{url}">{title}</a></b>']
    if date:
        lines.append(f"📅 {date}")
    if subtitle:
        lines.append(f"\n{subtitle}")
    lines.append(f'\n<a href="{url}">Read more →</a>')

    resp = requests.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json={
            "chat_id": channel_id,
            "text": "\n".join(lines),
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
