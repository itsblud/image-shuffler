#!/usr/bin/env python3
"""
Shuffler lightweight source audit v2

Diagnostic only:
- does NOT touch the live catalog, the site, or any deployed file
- Wayback CDX metadata only; no image payloads are ever downloaded
- at most 2 probes per source:
    Probe A  broad period 1994-2008, limit 100 unique digests
    Probe B  narrower era window, only if Probe A fails or looks weak
- image/CDN asset hosts are derived from returned original URLs (no extra requests)

Output: audit-output/source-audit.{csv,json,md}

Usage:
    python audit_sources.py                # full audit (~43-86 CDX requests)
    python audit_sources.py --only A,B     # audit just some sources (smoke tests)
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_OUT = ROOT / "audit-output"

USER_AGENT = "Shuffler-Source-Audit/2.0 (lightweight diagnostic)"
PERIOD = (1994, 2008)
PROBE_LIMIT = 100
WEAK_THRESHOLD = 30
MAX_WORKERS = 2
TIMEOUT = 45

# (source, domain, region, era)
# era picks the Probe B fallback window: early -> 1998-2003, late -> 2004-2008
SOURCES = [
    # Black / North American web culture
    ("BlackPlanet", "blackplanet.com", "North America / Black web", "early"),
    ("BlackVoices", "blackvoices.com", "North America / Black web", "early"),
    ("Okayplayer", "okayplayer.com", "North America / Black web", "early"),
    ("AllHipHop", "allhiphop.com", "North America / Black web", "early"),

    # Africa
    ("GhanaWeb", "ghanaweb.com", "Africa", "early"),
    ("Nairaland", "nairaland.com", "Africa", "late"),
    ("HiPipo", "hipipo.com", "Africa", "late"),
    ("Mashada", "mashada.com", "Africa", "early"),

    # South Asia
    ("Rediff", "rediff.com", "South Asia", "early"),
    ("Rediff iShare", "ishare.rediff.com", "South Asia", "late"),
    ("Sify", "sify.com", "South Asia", "early"),
    ("Sulekha", "sulekha.com", "South Asia", "early"),
    ("Indiatimes", "indiatimes.com", "South Asia", "early"),
    ("BigAdda", "bigadda.com", "South Asia", "late"),
    ("Ibibo", "ibibo.com", "South Asia", "late"),
    ("BharatStudent", "bharatstudent.com", "South Asia", "late"),
    ("Orkut", "orkut.com", "Global / strong Brazil + India usage", "late"),

    # East Asia
    ("Cyworld", "cyworld.com", "East Asia", "early"),
    ("Mixi", "mixi.jp", "East Asia", "late"),
    ("Xiaonei", "xiaonei.com", "East Asia", "late"),
    ("51.com", "51.com", "East Asia", "late"),
    ("QQ", "qq.com", "East Asia", "early"),

    # Latin America / Caribbean
    ("Fotolog", "fotolog.com", "Latin America / Caribbean", "late"),
    ("MetroFLOG", "metroflog.com", "Latin America / Caribbean", "late"),
    ("Flogao", "flogao.com.br", "Latin America / Caribbean", "late"),
    ("MiGente", "migente.com", "Latin America / Caribbean + US Latino", "late"),
    ("Hi5", "hi5.com", "Global / strong Latin America usage", "late"),

    # MENA
    ("Maktoob", "maktoob.com", "MENA", "early"),
    ("Jeeran", "jeeran.com", "MENA", "early"),

    # Global legacy image hosts
    ("TinyPic", "tinypic.com", "Global", "late"),
    ("Photobucket", "photobucket.com", "Global", "late"),
    ("Flickr static", "static.flickr.com", "Global", "late"),
    ("ImageShack", "imageshack.us", "Global", "late"),
    ("Fotki", "fotki.com", "Global", "early"),
    ("PBase", "pbase.com", "Global", "early"),
    ("PictureTrail", "picturetrail.com", "Global", "early"),
    ("Webshots", "webshots.com", "Global", "early"),
    ("Photo.net", "photo.net", "Global", "early"),
    ("SmugMug", "smugmug.com", "Global", "late"),
    ("Zooomr", "zooomr.com", "Global", "late"),
    ("GeoCities", "geocities.com", "Global", "early"),
    ("Angelfire", "angelfire.com", "Global", "early"),
    ("Tripod", "tripod.com", "Global", "early"),
]

ERA_WINDOWS = {"early": (1998, 2003), "late": (2004, 2008)}


def cdx_url(domain: str, start: int, end: int) -> str:
    params = [
        ("url", domain),
        ("matchType", "domain"),
        ("output", "json"),
        ("fl", "timestamp,original,mimetype,statuscode,digest,length"),
        ("from", str(start)),
        ("to", str(end)),
        ("filter", "statuscode:200"),
        ("filter", "mimetype:image/.*"),
        ("collapse", "digest"),
        ("limit", str(PROBE_LIMIT)),
    ]
    return "https://web.archive.org/cdx/search/cdx?" + urllib.parse.urlencode(params)


def fetch_cdx(url: str):
    """One attempt + one immediate retry. Returns (rows, error)."""
    last_error = ""
    for attempt in range(2):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"},
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
                data = json.loads(response.read().decode("utf-8", "replace"))
            rows = [r for r in (data[1:] if isinstance(data, list) else []) if isinstance(r, list) and len(r) >= 6]
            return rows, ""
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt == 0:
                time.sleep(1.0)
    return [], last_error


def audit_source(name: str, domain: str, region: str, era: str):
    probes = []
    rows_all = []

    rows_a, err_a = fetch_cdx(cdx_url(domain, PERIOD[0], PERIOD[1]))
    probes.append({
        "probe": "A",
        "window": list(PERIOD),
        "ok": not err_a,
        "error": err_a,
        "rows": len(rows_a),
        "capped": len(rows_a) >= PROBE_LIMIT,
    })
    rows_all.extend(rows_a)

    capped = probes[0]["capped"]
    digests = {r[4] for r in rows_all if len(r) > 4 and r[4]}
    if not capped and (err_a or len(digests) < WEAK_THRESHOLD):
        b_start, b_end = ERA_WINDOWS[era]
        rows_b, err_b = fetch_cdx(cdx_url(domain, b_start, b_end))
        probes.append({
            "probe": "B",
            "window": [b_start, b_end],
            "ok": not err_b,
            "error": err_b,
            "rows": len(rows_b),
            "capped": len(rows_b) >= PROBE_LIMIT,
        })
        rows_all.extend(rows_b)

    digests = {r[4] for r in rows_all if len(r) > 4 and r[4]}
    years = sorted({int(r[0][:4]) for r in rows_all if r[0] and r[0][:4].isdigit() and PERIOD[0] <= int(r[0][:4]) <= PERIOD[1]})
    capped = any(p["capped"] for p in probes)

    hosts = Counter()
    for r in rows_all:
        host = urllib.parse.urlparse(r[1]).hostname or ""
        if host:
            hosts[host.lower()] += 1

    root = domain.lower()
    asset_hosts = [(h, c) for h, c in hosts.most_common(20) if h != root and h != "www." + root][:5]

    return {
        "source": name,
        "domain": domain,
        "region": region,
        "probes": probes,
        "requests": len(probes),
        "rows": sum(p["rows"] for p in probes),
        "unique_digests": len(digests),
        "capped": capped,
        "years_seen": years,
        "asset_domains": [{"host": h, "count": c} for h, c in asset_hosts],
        "status": status_for(probes, len(digests)),
    }


def status_for(probes, digest_count: int) -> str:
    if not any(p["ok"] for p in probes):
        return "ERROR"
    capped = any(p["capped"] for p in probes)
    if digest_count == 0:
        return "NO HITS"
    if digest_count >= 150 or (capped and digest_count >= 100):
        return "STRONG"
    if capped or digest_count >= 50:
        return "PROMISING"
    return "SMALL"


def write_reports(results, out_dir: Path, total_requests: int):
    out_dir.mkdir(parents=True, exist_ok=True)

    results = sorted(results, key=lambda r: (r["unique_digests"], r["rows"]), reverse=True)

    by_status = Counter(r["status"] for r in results)

    (out_dir / "source-audit.json").write_text(
        json.dumps(
            {
                "generated_unix": int(time.time()),
                "period": list(PERIOD),
                "probe_limit": PROBE_LIMIT,
                "cdx_requests_made": total_requests,
                "sources_tested": len(results),
                "status_counts": dict(by_status),
                "note": (
                    "Metadata-only probe. unique_digests is a lower-bound sample; "
                    "a capped probe hit the row limit, so the real source is larger."
                ),
                "sources": results,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with (out_dir / "source-audit.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "source", "domain", "region", "status",
            "cdx_requests", "rows", "unique_digests", "capped",
            "years_seen", "asset_domains", "errors",
        ])
        for r in results:
            writer.writerow([
                r["source"], r["domain"], r["region"], r["status"],
                r["requests"], r["rows"], r["unique_digests"], "yes" if r["capped"] else "no",
                " ".join(map(str, r["years_seen"])),
                "; ".join(f'{a["host"]}({a["count"]})' for a in r["asset_domains"]),
                " | ".join(p["error"] for p in r["probes"] if p["error"]),
            ])

    lines = [
        "# Shuffler Source Audit (lightweight)",
        "",
        f"Period: {PERIOD[0]}-{PERIOD[1]}  ",
        f"CDX metadata requests made: {total_requests}  ",
        f"Sources tested: {len(results)}",
        "",
        "Each source gets one broad probe (up to "
        f"{PROBE_LIMIT} unique digests). A second narrower probe runs only when the "
        "first fails or returns fewer than "
        f"{WEAK_THRESHOLD} results. **Cap hit** means the probe reached the "
        f"{PROBE_LIMIT}-row limit, so the true source is larger.",
        "",
        "| Source | Region | Probe hits | Cap hit | Years seen | Asset domains | Status |",
        "|---|---|---:|---|---|---|---|",
    ]

    for r in results:
        if r["years_seen"]:
            yrs = f'{r["years_seen"][0]}-{r["years_seen"][-1]} ({len(r["years_seen"])})'
        else:
            yrs = "-"
        assets = "; ".join(a["host"] for a in r["asset_domains"][:3]) or "-"
        lines.append(
            f'| {r["source"]} | {r["region"]} | {r["unique_digests"]} | '
            f'{"YES" if r["capped"] else "no"} | {yrs} | {assets} | {r["status"]} |'
        )

    lines += [
        "",
        "## Status counts",
        "",
        "| Status | Sources |",
        "|---|---:|",
    ]
    for status in ("STRONG", "PROMISING", "SMALL", "NO HITS", "ERROR"):
        lines.append(f"| {status} | {by_status.get(status, 0)} |")

    lines += [
        "",
        "STRONG = clearly worth a production crawl. PROMISING = investigate deeper. "
        "SMALL = some material, not enough to anchor a region. NO HITS = archive "
        "answered but no image metadata found (may be queried at the wrong domain). "
        "ERROR = archive queries failed.",
        "",
        "This audit is read-only; it does not alter the live Shuffler catalog.",
    ]

    (out_dir / "source-audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Shuffler lightweight source audit")
    parser.add_argument("--only", default="", help="comma-separated source names to audit (for smoke tests)")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="output directory")
    args = parser.parse_args()

    sources = SOURCES
    if args.only:
        wanted = {w.strip().lower() for w in args.only.split(",") if w.strip()}
        sources = [s for s in SOURCES if s[0].lower() in wanted]
        if not sources:
            raise SystemExit(f"--only matched none of the {len(SOURCES)} known sources")

    print(f"Auditing {len(sources)} sources (max 2 CDX metadata probes each)")
    print("Read-only diagnostic; catalog and site are untouched.\n")

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(audit_source, *s): s[0] for s in sources}
        done = 0
        for future in concurrent.futures.as_completed(futures):
            name = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "source": name,
                    "domain": next((s[1] for s in sources if s[0] == name), ""),
                    "region": next((s[2] for s in sources if s[0] == name), ""),
                    "probes": [{"probe": "A", "window": list(PERIOD), "ok": False,
                                "error": f"{type(exc).__name__}: {exc}", "rows": 0, "capped": False}],
                    "requests": 1, "rows": 0, "unique_digests": 0, "capped": False,
                    "years_seen": [], "asset_domains": [], "status": "ERROR",
                }
            results.append(result)
            done += 1
            print(f'[{done}/{len(sources)}] {result["source"]:<16} '
                  f'digests={result["unique_digests"]:>4}  {"CAP " if result["capped"] else "    "}{result["status"]}')

    total_requests = sum(r["requests"] for r in results)
    out_dir = Path(args.out)
    write_reports(results, out_dir, total_requests)

    print(f"\nCDX metadata requests made: {total_requests}")
    print(f"Reports written to: {out_dir}")
    print("source-audit.csv / source-audit.json / source-audit.md")


if __name__ == "__main__":
    main()
