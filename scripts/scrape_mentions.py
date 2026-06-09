import json
import os
import re
import time
import hashlib
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import requests
import yaml
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
    base = f"{url}|{title}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_existing(path):
    if not os.path.exists(path):
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("mentions", [])
    except Exception:
        return []


def save_company_file(path, company_name, slug, mentions):
    payload = {
        "company": company_name,
        "slug": slug,
        "last_updated": utc_now_iso(),
        "count": len(mentions),
        "mentions": mentions,
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def fetch_gdelt_mentions(query, lookback_days, max_results):
    """
    Uses GDELT 2.1 DOC API.
    This avoids scraping individual websites and instead queries a public media database.
    """
    start_dt = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    start_str = start_dt.strftime("%Y%m%d%H%M%S")

    encoded_query = quote_plus(query)

    url = (
        "https://api.gdeltproject.org/api/v2/doc/doc"
        f"?query={encoded_query}"
        "&mode=artlist"
        "&format=json"
        f"&maxrecords={max_results}"
        f"&startdatetime={start_str}"
        "&sort=hybridrel"
    )

    headers = {
        "User-Agent": "media-mentions-tracker/1.0"
    }

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()

    data = response.json()
    articles = data.get("articles", [])

    mentions = []

    for article in articles:
        title = article.get("title") or ""
        article_url = article.get("url") or ""
        source = article.get("sourceCountry") or article.get("domain") or ""
        domain = article.get("domain") or ""
        language = article.get("language") or ""
        published_raw = article.get("seendate") or article.get("socialimage") or ""

        published_at = None
        if article.get("seendate"):
            try:
                published_at = date_parser.parse(article["seendate"]).replace(
                    tzinfo=timezone.utc
                ).isoformat()
            except Exception:
                published_at = article.get("seendate")

        if not title or not article_url:
            continue

        mentions.append({
            "id": make_id(article_url, title),
            "title": title,
            "url": article_url,
            "domain": domain,
            "source": source,
            "language": language,
            "published_at": published_at,
            "summary": article.get("snippet") or "",
            "image": article.get("socialimage") or None,
            "source_api": "gdelt",
            "collected_at": utc_now_iso(),
        })

    return mentions


def merge_mentions(existing, incoming):
    by_id = {}

    for item in existing + incoming:
        item_id = item.get("id")
        if not item_id:
            continue
        by_id[item_id] = item

    merged = list(by_id.values())

    def sort_key(item):
        return item.get("published_at") or item.get("collected_at") or ""

    merged.sort(key=sort_key, reverse=True)
    return merged


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
    companies = config.get("companies", [])
    settings = config.get("settings", {})

    max_results = settings.get("max_results_per_company", 50)
    lookback_days = settings.get("lookback_days", 7)

    summaries = []

    for company in companies:
        name = company["name"]
        query = company.get("query", name)
        slug = company.get("slug") or clean_slug(name)

        print(f"Fetching mentions for {name}...")

        company_path = os.path.join(COMPANY_DIR, f"{slug}.json")

        existing = load_existing(company_path)

        try:
            incoming = fetch_gdelt_mentions(
                query=query,
                lookback_days=lookback_days,
                max_results=max_results,
            )
        except Exception as e:
            print(f"Error fetching {name}: {e}")
            incoming = []

        merged = merge_mentions(existing, incoming)

        save_company_file(company_path, name, slug, merged)

        summaries.append({
            "name": name,
            "slug": slug,
            "file": f"data/companies/{slug}.json",
            "count": len(merged),
            "latest_mention": merged[0] if merged else None,
        })

        time.sleep(2)

    build_index(summaries)

    print("Done.")


if __name__ == "__main__":
    main()
