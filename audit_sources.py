#!/usr/bin/env python3
"""
Shuffler source audit v1

Diagnostic only:
- does NOT touch the live catalog
- does NOT deploy the site
- queries Wayback CDX metadata only
- checks each intended source year-by-year from 1994-2008
- writes CSV, JSON and Markdown reports

This is intentionally a PROBE, not the production crawler.
A source hitting the per-year cap is marked as capped, meaning:
"there is at least this much material; crawl deeper later."
"""

from __future__ import annotations

import concurrent.futures
import csv
import json
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "source-audit-report"
OUT.mkdir(exist_ok=True)

USER_AGENT = "Shuffler-Source-Audit/1.0"
START_YEAR = 1994
END_YEAR = 2008
PER_YEAR_LIMIT = 500
MAX_WORKERS = 4
TIMEOUT = 18
RETRIES = 2

SOURCES = [
    # Black / North American web culture
    {"source": "BlackPlanet", "domain": "blackplanet.com", "region": "North America / Black web"},
    {"source": "BlackVoices", "domain": "blackvoices.com", "region": "North America / Black web"},
    {"source": "Okayplayer", "domain": "okayplayer.com", "region": "North America / Black web"},
    {"source": "AllHipHop", "domain": "allhiphop.com", "region": "North America / Black web"},
    {"source": "MiGente", "domain": "migente.com", "region": "Latin America / Caribbean + US Latino"},

    # Africa
    {"source": "GhanaWeb", "domain": "ghanaweb.com", "region": "Africa"},
    {"source": "Nairaland", "domain": "nairaland.com", "region": "Africa"},
    {"source": "HiPipo", "domain": "hipipo.com", "region": "Africa"},
    {"source": "Mashada", "domain": "mashada.com", "region": "Africa"},

    # South Asia
    {"source": "Rediff", "domain": "rediff.com", "region": "South Asia"},
    {"source": "Rediff iShare", "domain": "ishare.rediff.com", "region": "South Asia"},
    {"source": "Sify", "domain": "sify.com", "region": "South Asia"},
    {"source": "Sulekha", "domain": "sulekha.com", "region": "South Asia"},
    {"source": "Indiatimes", "domain": "indiatimes.com", "region": "South Asia"},
    {"source": "BigAdda", "domain": "bigadda.com", "region": "South Asia"},
    {"source": "Ibibo", "domain": "ibibo.com", "region": "South Asia"},
    {"source": "BharatStudent", "domain": "bharatstudent.com", "region": "South Asia"},

    # East Asia
    {"source": "Cyworld", "domain": "cyworld.com", "region": "East Asia"},
    {"source": "Mixi", "domain": "mixi.jp", "region": "East Asia"},
    {"source": "Xiaonei", "domain": "xiaonei.com", "region": "East Asia"},
    {"source": "51.com", "domain": "51.com", "region": "East Asia"},
    {"source": "QQ", "domain": "qq.com", "region": "East Asia"},
    {"source": "Orkut", "domain": "orkut.com", "region": "Global / strong Brazil + India usage"},

    # Latin America / Caribbean
    {"source": "Fotolog", "domain": "fotolog.com", "region": "Latin America / Caribbean"},
    {"source": "MetroFLOG", "domain": "metroflog.com", "region": "Latin America / Caribbean"},
    {"source": "Flogao", "domain": "flogao.com.br", "region": "Latin America / Caribbean"},
    {"source": "Hi5", "domain": "hi5.com", "region": "Global / strong Latin America usage"},

    # MENA
    {"source": "Maktoob", "domain": "maktoob.com", "region": "MENA"},
    {"source": "Jeeran", "domain": "jeeran.com", "region": "MENA"},

    # Global old-web / image hosts
    {"source": "TinyPic", "domain": "tinypic.com", "region": "Global"},
    {"source": "Photobucket", "domain": "photobucket.com", "region": "Global"},
    {"source": "Flickr static", "domain": "static.flickr.com", "region": "Global"},
    {"source": "ImageShack", "domain": "imageshack.us", "region": "Global"},
    {"source": "Fotki", "domain": "fotki.com", "region": "Global"},
    {"source": "PBase", "domain": "pbase.com", "region": "Global"},
    {"source": "PictureTrail", "domain": "picturetrail.com", "region": "Global"},
    {"source": "Webshots", "domain": "webshots.com", "region": "Global"},
    {"source": "Photo.net", "domain": "photo.net", "region": "Global"},
    {"source": "SmugMug", "domain": "smugmug.com", "region": "Global"},
    {"source": "Zooomr", "domain": "zooomr.com", "region": "Global"},
    {"source": "GeoCities", "domain": "geocities.com", "region": "Global"},
    {"source": "Angelfire", "domain": "angelfire.com", "region": "Global"},
    {"source": "Tripod", "domain": "tripod.com", "region": "Global"},
]


def fetch_json(url: str):
    delay = 1.5
    last_error = None
    for attempt in range(RETRIES + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json,text/plain,*/*",
                },
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8", "replace"))
        except Exception as exc:
            last_error = exc
            if attempt >= RETRIES:
                raise
            time.sleep(delay)
            delay *= 2
    raise last_error


def audit_year(source: str, domain: str, region: str, year: int):
    params = [
        ("url", domain),
        ("matchType", "domain"),
        ("output", "json"),
        ("fl", "timestamp,original,mimetype,statuscode,digest,length"),
        ("from", str(year)),
        ("to", str(year)),
        ("filter", "statuscode:200"),
        ("filter", "mimetype:image/.*"),
        ("collapse", "digest"),
        ("limit", str(PER_YEAR_LIMIT)),
    ]
    url = "https://web.archive.org/cdx/search/cdx?" + urllib.parse.urlencode(params)

    try:
        data = fetch_json(url)
    except Exception as exc:
        return {
            "source": source,
            "domain": domain,
            "region": region,
            "year": year,
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "rows": [],
            "count": 0,
            "capped": False,
        }

    rows = data[1:] if isinstance(data, list) and data else []
    cleaned = [row for row in rows if isinstance(row, list) and len(row) >= 6]

    return {
        "source": source,
        "domain": domain,
        "region": region,
        "year": year,
        "ok": True,
        "error": "",
        "rows": cleaned,
        "count": len(cleaned),
        "capped": len(cleaned) >= PER_YEAR_LIMIT,
    }


def status_for(unique_digests: int, capped_years: int, failed_years: int):
    if unique_digests == 0:
        return "ERROR / EMPTY" if failed_years else "NO HITS"
    if capped_years >= 2 or unique_digests >= 1500:
        return "STRONG"
    if capped_years >= 1 or unique_digests >= 500:
        return "PROMISING"
    return "SMALL"


def main():
    jobs = []
    for s in SOURCES:
        for year in range(START_YEAR, END_YEAR + 1):
            jobs.append((s["source"], s["domain"], s["region"], year))

    print(f"Auditing {len(SOURCES)} sources across {END_YEAR - START_YEAR + 1} years")
    print(f"{len(jobs)} CDX metadata queries, {MAX_WORKERS} at a time")
    print("This is diagnostic only; it does not modify the live catalog.\n")

    results = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(audit_year, *job) for job in jobs]
        for n, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            result = future.result()
            results.append(result)
            if result["ok"]:
                cap = " CAP" if result["capped"] else ""
                print(f'[{n}/{len(jobs)}] {result["source"]} {result["year"]}: {result["count"]}{cap}')
            else:
                print(f'[{n}/{len(jobs)}] {result["source"]} {result["year"]}: ERROR {result["error"]}')

    grouped = defaultdict(list)
    for r in results:
        grouped[r["source"]].append(r)

    summary = []

    for s in SOURCES:
        name = s["source"]
        yearly = sorted(grouped[name], key=lambda x: x["year"])

        urls = set()
        digests = set()
        mimetypes = defaultdict(int)
        years_with_hits = []
        capped_years = []
        failed_years = []

        sampled_rows = 0

        for y in yearly:
            if not y["ok"]:
                failed_years.append(y["year"])
                continue
            if y["count"]:
                years_with_hits.append(y["year"])
            if y["capped"]:
                capped_years.append(y["year"])

            for row in y["rows"]:
                sampled_rows += 1
                timestamp, original, mimetype, statuscode, digest, length = row[:6]
                if original:
                    urls.add(original)
                if digest:
                    digests.add(digest)
                mimetypes[mimetype or "unknown"] += 1

        record = {
            "source": name,
            "domain": s["domain"],
            "region": s["region"],
            "sampled_rows": sampled_rows,
            "unique_urls": len(urls),
            "unique_digests": len(digests),
            "years_with_hits": years_with_hits,
            "capped_years": capped_years,
            "failed_years": failed_years,
            "status": status_for(len(digests), len(capped_years), len(failed_years)),
            "top_mimetypes": sorted(
                mimetypes.items(),
                key=lambda kv: kv[1],
                reverse=True,
            )[:5],
        }
        summary.append(record)

    summary.sort(key=lambda x: (x["unique_digests"], x["unique_urls"]), reverse=True)

    region_totals = defaultdict(lambda: {
        "sources": 0,
        "unique_digests_sum": 0,
        "sources_with_hits": 0,
        "strong_or_promising": 0,
    })

    for row in summary:
        r = region_totals[row["region"]]
        r["sources"] += 1
        r["unique_digests_sum"] += row["unique_digests"]
        if row["unique_digests"] > 0:
            r["sources_with_hits"] += 1
        if row["status"] in {"STRONG", "PROMISING"}:
            r["strong_or_promising"] += 1

    json_path = OUT / "source-audit.json"
    csv_path = OUT / "source-audit.csv"
    md_path = OUT / "source-audit.md"

    json_path.write_text(
        json.dumps(
            {
                "generated_unix": int(time.time()),
                "period": [START_YEAR, END_YEAR],
                "per_year_limit": PER_YEAR_LIMIT,
                "note": (
                    "Counts are audit lower bounds / samples. A capped year means "
                    "the source has more results than this probe retrieved."
                ),
                "sources": summary,
                "region_totals": dict(region_totals),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "source", "domain", "region", "status",
            "sampled_rows", "unique_urls", "unique_digests",
            "years_with_hits", "capped_years", "failed_years"
        ])
        for row in summary:
            writer.writerow([
                row["source"],
                row["domain"],
                row["region"],
                row["status"],
                row["sampled_rows"],
                row["unique_urls"],
                row["unique_digests"],
                " ".join(map(str, row["years_with_hits"])),
                " ".join(map(str, row["capped_years"])),
                " ".join(map(str, row["failed_years"])),
            ])

    lines = [
        "# Shuffler Source Audit",
        "",
        f"Period: {START_YEAR}–{END_YEAR}",
        "",
        (
            f"Each source is probed year-by-year, up to {PER_YEAR_LIMIT} unique-content "
            "CDX rows per year. **CAP** means the audit hit the limit, so the real source "
            "is larger than the reported probe."
        ),
        "",
        "| Source | Region | Unique digests | Unique URLs | Hit years | Capped years | Failed years | Status |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]

    for row in summary:
        lines.append(
            f'| {row["source"]} | {row["region"]} | {row["unique_digests"]} | '
            f'{row["unique_urls"]} | {len(row["years_with_hits"])} | '
            f'{len(row["capped_years"])} | {len(row["failed_years"])} | {row["status"]} |'
        )

    lines += ["", "## Region summary", "",
              "| Region | Sources tested | Sources with hits | Strong/promising | Sum of sampled unique digests |",
              "|---|---:|---:|---:|---:|"]

    for region, r in sorted(region_totals.items()):
        lines.append(
            f'| {region} | {r["sources"]} | {r["sources_with_hits"]} | '
            f'{r["strong_or_promising"]} | {r["unique_digests_sum"]} |'
        )

    lines += [
        "",
        "## How to read this",
        "",
        "- **STRONG**: clearly worth a production crawl.",
        "- **PROMISING**: enough material to investigate deeper.",
        "- **SMALL**: some usable archive material, but not enough to anchor a region.",
        "- **NO HITS**: no matching archived image metadata found in this probe.",
        "- **ERROR / EMPTY**: the source could not be evaluated reliably because queries failed.",
        "",
        "This report does not alter the live Shuffler catalog.",
    ]

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n=== TOP SOURCES ===")
    for row in summary[:15]:
        print(
            f'{row["source"]:<18} {row["unique_digests"]:>5} digests  '
            f'{len(row["capped_years"]):>2} capped years  {row["status"]}'
        )

    print("\nReports written to:", OUT)


if __name__ == "__main__":
    main()
