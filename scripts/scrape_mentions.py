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
def parse_gdelt_date(value):
    if not value:
        return None

    try:
        return date_parser.parse(value).replace(tzinfo=timezone.utc).isoformat()
    except Exception:
        return value

def fetch_gdelt_mentions(query, lookback_days, max_results):
    encoded_query = quote_plus(query)

    url = (
        "https://api.gdeltproject.org/api/v2/doc/doc"
        f"?query={encoded_query}"
        "&mode=artlist"
        "&format=json"
        f"&maxrecords={max_results}"
        f"&timespan={lookback_days}d"
        "&sort=datedesc"
    )

    headers = {
        "User-Agent": "media-mentions-tracker/1.0 xanderfarrington"
    }

    for attempt in range(5):
        print(f"Attempt {attempt + 1}/5")
        print(f"GDELT URL: {url}")

        response = requests.get(url, headers=headers, timeout=45)

        if response.status_code == 429:
            wait_time = 60 * (attempt + 1)
            print(f"Rate limited by GDELT. Waiting {wait_time} seconds...")
            time.sleep(wait_time)
            continue

        response.raise_for_status()

        data = response.json()
        articles = data.get("articles", [])

        print(f"Articles returned: {len(articles)}")

        mentions = []

        for article in articles:
            title = article.get("title")
            article_url = article.get("url")

            if not title or not article_url:
                continue

            mentions.append({
                "id": make_id(article_url, title),
                "title": title,
                "url": article_url,
                "domain": article.get("domain"),
                "source_country": article.get("sourceCountry"),
                "language": article.get("language"),
                "published_at": parse_gdelt_date(article.get("seendate")),
                "image": article.get("socialimage"),
                "source_api": "gdelt",
                "collected_at": utc_now_iso(),
            })

        return mentions

    raise RuntimeError("GDELT rate limit persisted after 5 attempts.")


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

    max_results = settings.get("max_results_per_company", 20)
    lookback_days = settings.get("lookback_days", 14)

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

        time.sleep(30)

    build_index(summaries)

    print("Done.")


if __name__ == "__main__":
    main()
