#!/usr/bin/env python3
"""Daily copper news fetcher and Telegram notifier.

Fetches yesterday's copper articles from mining.com,
translates titles to Chinese, and posts to a Telegram channel.

Runs via GitHub Actions — no local machine required.
"""

import os
import sys
import calendar
import requests
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator


# ── Helpers ──────────────────────────────────────────────────────────────────

def get_yesterday_gmt8():
    gmt8 = timezone(timedelta(hours=8))
    today = datetime.now(gmt8).date()
    return today - timedelta(days=1)


def date_display(d):
    """Return 'March 10, 2026' (no zero-padded day, cross-platform)."""
    return f"{calendar.month_name[d.month]} {d.day}, {d.year}"


# ── Fetching ──────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

RSS_CANDIDATES = [
    "https://www.mining.com/commodity/copper/feed/",
    "https://www.mining.com/feed/?category=copper",
]


def fetch_via_rss(target_date):
    """Try RSS feeds. Returns list of articles or None on failure."""
    gmt8 = timezone(timedelta(hours=8))

    for rss_url in RSS_CANDIDATES:
        try:
            resp = requests.get(rss_url, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                continue

            soup = BeautifulSoup(resp.content, "lxml-xml")
            items = soup.find_all("item")
            if not items:
                continue

            articles = []
            for item in items:
                pub_date_tag = item.find("pubDate")
                if not pub_date_tag:
                    continue
                try:
                    dt = parsedate_to_datetime(pub_date_tag.get_text())
                    article_date = dt.astimezone(gmt8).date()
                except Exception:
                    continue

                if article_date != target_date:
                    continue

                title_tag = item.find("title")
                link_tag = item.find("link")
                if title_tag and link_tag:
                    title = title_tag.get_text(strip=True)
                    link = link_tag.get_text(strip=True)
                    if title and link:
                        articles.append({"title": title, "url": link})

            if articles:
                print(f"RSS OK ({rss_url}): {len(articles)} articles")
                return articles

        except Exception as e:
            print(f"RSS {rss_url} error: {e}")

    return None


def fetch_via_html(target_date):
    """Scrape HTML page. Returns list of articles (may be empty)."""
    url = "https://www.mining.com/commodity/copper/"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    target_str = date_display(target_date)
    target_iso = target_date.isoformat()

    articles = []
    seen_urls = set()

    def add_article(title_elem, link_elem):
        title = title_elem.get_text(strip=True)
        href = link_elem.get("href", "")
        if not href.startswith("http"):
            href = "https://www.mining.com" + href
        if href not in seen_urls and "mining.com" in href and len(title) > 10:
            seen_urls.add(href)
            articles.append({"title": title, "url": href})

    def walk_up_for_article(start_elem):
        container = start_elem
        for _ in range(7):
            container = container.parent
            if container is None:
                break
            title_elem = container.find(["h2", "h3", "h4"])
            if title_elem:
                link = title_elem.find("a", href=True) or container.find("a", href=True)
                if link:
                    add_article(title_elem, link)
                    return

    # Strategy 1: <time> elements with datetime attribute or text match
    for time_elem in soup.find_all("time"):
        dt_attr = time_elem.get("datetime", "")
        text = time_elem.get_text(strip=True)
        if target_iso in dt_attr or target_str in text:
            walk_up_for_article(time_elem)

    # Strategy 2: any element whose text contains the date string
    if not articles:
        for node in soup.find_all(string=lambda t: t and target_str in t):
            walk_up_for_article(node.parent)

    print(f"HTML scrape: {len(articles)} articles")
    return articles


# ── Translation ───────────────────────────────────────────────────────────────

def translate(text):
    try:
        return GoogleTranslator(source="en", target="zh-CN").translate(text)
    except Exception as e:
        print(f"Translation error for '{text[:40]}...': {e}")
        return text  # fall back to original


# ── Message ───────────────────────────────────────────────────────────────────

def build_message(articles, date_str):
    if not articles:
        return f"⚠️ 昨日（{date_str}）暂无铜矿新闻"

    lines = [f"\U0001f4f0 铜矿新闻 {date_str}", ""]
    for article in articles:
        zh = translate(article["title"])
        lines.append(f"[{article['title']}]({article['url']})")
        lines.append(zh)
        lines.append("")

    return "\n".join(lines).strip()


# ── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram(bot_token, channel_id, message):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    resp = requests.post(
        url,
        json={
            "chat_id": channel_id,
            "text": message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": False,
        },
        timeout=30,
    )
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data}")
    return data


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    bot_token = os.environ.get("BOT_TOKEN", "")
    channel_id = os.environ.get("CHANNEL_ID", "")

    if not bot_token or not channel_id:
        print("ERROR: BOT_TOKEN and CHANNEL_ID environment variables are required.")
        sys.exit(1)

    yesterday = get_yesterday_gmt8()
    date_str = yesterday.isoformat()
    print(f"Fetching copper news for {date_str} ({date_display(yesterday)}) ...")

    articles = fetch_via_rss(yesterday)
    if articles is None:
        print("RSS unavailable, falling back to HTML scraping ...")
        articles = fetch_via_html(yesterday)

    message = build_message(articles, date_str)
    print("--- Message ---")
    print(message)
    print("---------------")

    result = send_telegram(bot_token, channel_id, message)
    print(f"Sent OK: {result.get('ok')}")


if __name__ == "__main__":
    main()
