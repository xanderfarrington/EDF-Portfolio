import json
import os
import re
import time
import html
import hashlib
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus, urlparse

import yaml
import feedparser
from dateutil import parser as date_parser


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config", "companies.yml")
DATA_DIR = os.path.join(ROOT, "data")
COMPANY_DIR = os.path.join(DATA_DIR, "companies")
INDEX_PATH = os.path.join(DATA_DIR, "index.json")


def utc_now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clean_slug(value):
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def make_id(url, title):
    return hashlib.sha256(f"{url}|{title}".encode("utf-8")).hexdigest()[:16]


def get_domain(url):
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return None


def clean_text(value):
    if not value:
        return ""

    text = html.unescape(value)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()

    return text


def parse_date(value):
    if not value:
        return None

    try:
        parsed = date_parser.parse(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except Exception:
        return None


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    companies = config.get("companies", [])
    if not companies:
        raise ValueError("No companies found. config/companies.yml must start with 'companies:'")

    return config


def load_existing(path):
    if not os.path.exists(path):
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("mentions", [])
    except Exception:
        return []


def fetch_news_mentions(query, lookback_days, max_results):
    encoded_query = quote_plus(query)

    url = (
        "https://news.google.com/rss/search"
        f"?q={encoded_query}"
        "&hl=en-US&gl=US&ceid=US:en"
    )

    print(f"Google News RSS URL: {url}", flush=True)

    feed = feedparser.parse(url)

    if feed.bozo:
        print(f"Feed warning: {feed.bozo_exception}", flush=True)

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    mentions = []

    for entry in feed.entries[:max_results]:
        title = clean_text(entry.get("title", ""))
        article_url = entry.get("link")
        published_raw = entry.get("published")
        published_at = parse_date(published_raw)

        if not title or not article_url:
            continue

        if published_at:
            published_dt = date_parser.parse(published_at)
            if published_dt < cutoff:
                continue

        source = None
        if hasattr(entry, "source"):
            source = entry.source.get("title")

        raw_summary = entry.get("summary", "")
        clean_summary = clean_text(raw_summary)

        mentions.append({
            "id": make_id(article_url, title),
            "title": title,
            "url": article_url,
            "domain": get_domain(article_url),
            "source": source,
            "language": "English",
            "published_at": published_at,
            "summary": clean_summary,
            "raw_summary": raw_summary,
            "image": None,
            "source_api": "google_news_rss",
            "collected_at": utc_now_iso(),
        })

    print(f"Articles returned: {len(mentions)}", flush=True)
    return mentions


def merge_mentions(existing, incoming):
    by_id = {}

    for item in existing:
        if item.get("id"):
            by_id[item["id"]] = item

    for item in incoming:
        if item.get("id") and item["id"] not in by_id:
            by_id[item["id"]] = item

    merged = list(by_id.values())

    merged.sort(
        key=lambda x: x.get("published_at") or x.get("collected_at") or "",
        reverse=True,
    )

    return merged


def save_company_file(path, company_name, slug, query, mentions):
    payload = {
        "company": company_name,
        "slug": slug,
        "query": query,
        "last_updated": utc_now_iso(),
        "archive_policy": "append_only_no_deletion",
        "count": len(mentions),
        "mentions": mentions,
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def build_index(company_summaries):
    payload = {
        "last_updated": utc_now_iso(),
        "companies": company_summaries,
    }

    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def main():
    os.makedirs(COMPANY_DIR, exist_ok=True)

    config = load_config()
    companies = config["companies"]
    settings = config.get("settings", {})

    max_results = settings.get("max_results_per_company", 25)
    lookback_days = settings.get("lookback_days", 30)
    sleep_seconds = settings.get("sleep_seconds_between_companies", 5)

    summaries = []

    for company in companies:
        name = company["name"]
        query = company.get("query", name)
        slug = clean_slug(company.get("slug") or name)

        print("=" * 80, flush=True)
        print(f"Fetching mentions for {name}", flush=True)
        print(f"Query: {query}", flush=True)
        print("=" * 80, flush=True)

        company_path = os.path.join(COMPANY_DIR, f"{slug}.json")
        existing = load_existing(company_path)

        try:
            incoming = fetch_news_mentions(
                query=query,
                lookback_days=lookback_days,
                max_results=max_results,
            )
        except Exception as e:
            print(f"Error fetching {name}: {e}", flush=True)
            incoming = []

        merged = merge_mentions(existing, incoming)

        save_company_file(
            path=company_path,
            company_name=name,
            slug=slug,
            query=query,
            mentions=merged,
        )

        summaries.append({
            "name": name,
            "slug": slug,
            "file": f"data/companies/{slug}.json",
            "count": len(merged),
            "new_mentions_this_run": len(incoming),
            "latest_mention": merged[0] if merged else None,
        })

        time.sleep(sleep_seconds)

    build_index(summaries)
    print("Done.", flush=True)


if __name__ == "__main__":
    main()
