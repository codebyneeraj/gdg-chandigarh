"""Fetch openly licensed waste photos from Wikimedia Commons for the benchmark set.

Only files whose licence is public domain, CC0, CC BY or CC BY-SA are kept.
Every file's source URL, author and licence go to data/bench/sources.csv, which
feeds data/ATTRIBUTION.md. Images are re-encoded with Pillow so no EXIF/GPS
metadata survives.

Usage: python scripts/fetch_bench_images.py [--per-category 15]
"""

from __future__ import annotations

import argparse
import csv
import html
import io
import re
import time
from pathlib import Path

import httpx
from PIL import Image

API = "https://commons.wikimedia.org/w/api.php"
UA = {"User-Agent": "SaafRoute/0.1 (https://www.mediawiki.org/wiki/API:Etiquette; hackathon benchmark prep) httpx/0.28"}
CATEGORIES = [
    "Overflowing_waste_containers",
    "Illegal_dumping",
    "Waste_containers_in_India",
    "Garbage_dumps_in_India",
    "Litter_in_India",
    "Waste_in_India",
]
ALLOWED = re.compile(r"^(cc0|public domain|pd|cc by(-sa)? [0-9.]+|cc by(-sa)?)", re.I)
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "bench" / "images"


def strip_tags(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def polite_get(client: httpx.Client, url: str, **kw) -> httpx.Response:
    """GET with backoff on 429/5xx, honouring Retry-After (Wikimedia rate limits)."""
    for attempt in range(6):
        r = client.get(url, **kw)
        if r.status_code == 200:
            time.sleep(1.0)
            return r
        wait = float(r.headers.get("Retry-After", 0) or 0) or 10 * (attempt + 1)
        print(f"  HTTP {r.status_code}, waiting {wait:.0f}s")
        time.sleep(wait)
    r.raise_for_status()
    return r


def category_files(client: httpx.Client, cat: str) -> list[str]:
    r = polite_get(client, API, params={"action": "query", "list": "categorymembers", "cmtitle": f"Category:{cat}",
                                "cmtype": "file", "cmlimit": 500, "format": "json"})
    return [m["title"] for m in r.json()["query"]["categorymembers"]]


def file_info(client: httpx.Client, titles: list[str]) -> list[dict]:
    r = polite_get(client, API, params={"action": "query", "titles": "|".join(titles), "prop": "imageinfo",
                                "iiprop": "url|extmetadata|mime", "iiurlwidth": 800, "format": "json"})
    return list(r.json()["query"]["pages"].values())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-category", type=int, default=15)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    rows, seen = [], set()
    with httpx.Client(headers=UA, timeout=60, follow_redirects=True) as client:
        for cat in CATEGORIES:
            titles = [t for t in category_files(client, cat) if t.lower().endswith((".jpg", ".jpeg", ".png"))]
            kept = 0
            for i in range(0, len(titles), 40):
                if kept >= args.per_category:
                    break
                for page in file_info(client, titles[i:i + 40]):
                    if kept >= args.per_category:
                        break
                    title = page["title"]
                    if title in seen or "imageinfo" not in page:
                        continue
                    ii = page["imageinfo"][0]
                    meta = ii.get("extmetadata", {})
                    lic = strip_tags(meta.get("LicenseShortName", {}).get("value", ""))
                    if not ALLOWED.match(lic):
                        continue
                    seen.add(title)
                    try:
                        data = polite_get(client, ii["thumburl"]).content
                        img = Image.open(io.BytesIO(data)).convert("RGB")
                    except Exception as exc:  # noqa: BLE001 - skip unreadable files, keep going
                        print("skip", title, exc)
                        continue
                    img_id = f"cm{len(rows) + 1:03d}"
                    img.save(OUT / f"{img_id}.jpg", "JPEG", quality=88)  # fresh encode = no EXIF
                    rows.append({
                        "id": img_id, "category": cat, "title": title,
                        "source_url": ii["descriptionurl"],
                        "author": strip_tags(meta.get("Artist", {}).get("value", "unknown"))[:120],
                        "license": lic,
                        "license_url": meta.get("LicenseUrl", {}).get("value", ""),
                    })
                    kept += 1
            print(cat, kept)
    with open(ROOT / "data" / "bench" / "sources.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print("total", len(rows))


if __name__ == "__main__":
    main()
