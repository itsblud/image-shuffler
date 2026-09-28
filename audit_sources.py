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
- bounded runtime: a global deadline guarantees reports are written even when
  archive.org is slow; unreached sources are recorded explicitly as ERROR

Output: audit-output/source-audit.{csv,json,md}

Usage:
    python audit_sources.py                    # full audit (~43-86 CDX requests)
    python audit_sources.py --only A,B         # audit just some sources (smoke tests)
    python audit_sources.py --deadline-seconds 600
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
MAX_WORKERS = 3
TIMEOUT_FIRST = 30       # first attempt: fail fast on a slow day
TIMEOUT_RETRY = 75       # single retry: catches "slow but eventually answers"
DEADLINE_SECONDS = 1200  # stop launching work after this; reports still written

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

# reserve so a launched probe has time to finish before the deadline
PROBE_RESERVE = TIMEOUT_RETRY + 30


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
    """One bounded attempt plus a single longer retry. Returns (rows, error)."""
    last_error = ""
    for timeout in (TIMEOUT_FIRST, TIMEOUT_RETRY):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8", "replace"))
            rows = [r for r in (data[1:] if isinstance(data, list) else []) if isinstance(r, list) and len(r) >= 6]
            return rows, ""
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
    return [], last_error


def probe_a(name: str, domain: str, region: str, era: str):
    rows, err = fetch_cdx(cdx_url(domain, PERIOD[0], PERIOD[1]))
    record = {
        "source": name,
        "domain": domain,
        "region": region,
        "era": era,
        "probes": [{
            "probe": "A",
            "window": list(PERIOD),
            "ok": not err,
            "error": err,
            "rows": len(rows),
            "capped": len(rows) >= PROBE_LIMIT,
        }],
        "requests": 1,
    }
    return record, rows


def probe_b(record, rows_a):
    start, end = ERA_WINDOWS[record["era"]]
    rows_b, err = fetch_cdx(cdx_url(record["domain"], start, end))
    record["probes"].append({
        "probe": "B",
        "window": [start, end],
        "ok": not err,
        "error": err,
        "rows": len(rows_b),
        "capped": len(rows_b) >= PROBE_LIMIT,
    })
    record["requests"] += 1
    return rows_b


def finalize(record, rows_all):
    digests = {r[4] for r in rows_all if len(r) > 4 and r[4]}
    years = sorted({
        int(r[0][:4]) for r in rows_all
        if r[0] and r[0][:4].isdigit() and PERIOD[0] <= int(r[0][:4]) <= PERIOD[1]
    })

    hosts = Counter()
    for r in rows_all:
        host = urllib.parse.urlparse(r[1]).hostname or ""
        if host:
            hosts[host.lower()] += 1

    root = record["domain"].lower()
    asset_hosts = [(h, c) for h, c in hosts.most_common(20) if h != root and h != "www." + root][:5]

    record["rows"] = sum(p["rows"] for p in record["probes"])
    record["unique_digests"] = len(digests)
    record["capped"] = any(p["capped"] for p in record["probes"])
    record["years_seen"] = years
    record["asset_domains"] = [{"host": h, "count": c} for h, c in asset_hosts]
    record["status"] = status_for(record["probes"], len(digests))
    return record


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


def error_record(source, message):
    name, domain, region, era = source
    return {
        "source": name,
        "domain": domain,
        "region": region,
        "era": era,
        "probes": [{
            "probe": "A",
            "window": list(PERIOD),
            "ok": False,
            "error": message,
            "rows": 0,
            "capped": False,
        }],
        "requests": 0,
        "rows": 0,
        "unique_digests": 0,
        "capped": False,
        "years_seen": [],
        "asset_domains": [],
        "status": "ERROR",
    }


def write_reports(results, out_dir: Path, total_requests: int, deadline_note: str):
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
                "deadline_note": deadline_note,
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
        "ERROR = archive queries failed or the audit deadline was reached first.",
        "",
        "This audit is read-only; it does not alter the live Shuffler catalog.",
    ]

    if deadline_note:
        lines += ["", f"> {deadline_note}"]

    (out_dir / "source-audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Shuffler lightweight source audit")
    parser.add_argument("--only", default="", help="comma-separated source names to audit (for smoke tests)")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="output directory")
    parser.add_argument("--deadline-seconds", type=int, default=DEADLINE_SECONDS,
                        help="stop launching new probes after this many seconds (default 1200)")
    args = parser.parse_args()

    sources = SOURCES
    if args.only:
        wanted = {w.strip().lower() for w in args.only.split(",") if w.strip()}
        sources = [s for s in SOURCES if s[0].lower() in wanted]
        if not sources:
            raise SystemExit(f"--only matched none of the {len(SOURCES)} known sources")

    t0 = time.monotonic()
    deadline = args.deadline_seconds

    def remaining():
        return deadline - (time.monotonic() - t0)

    print(f"Auditing {len(sources)} sources (max 2 CDX metadata probes each)", flush=True)
    print("Read-only diagnostic; catalog and site are untouched.", flush=True)
    print(f"Deadline: {deadline}s (unreached sources are recorded as ERROR)\n", flush=True)

    records = {}
    rows_by_source = {}
    deadline_note = ""

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        # ---- Phase 1: broad probe for every source ----
        futures = {pool.submit(probe_a, *s): s for s in sources}
        phase1_done = 0
        try:
            for future in concurrent.futures.as_completed(futures, timeout=max(deadline + PROBE_RESERVE, 60)):
                source = futures[future]
                try:
                    record, rows = future.result()
                except Exception as exc:
                    record, rows = error_record(source, f"{type(exc).__name__}: {exc}"), []
                records[source[0]] = record
                rows_by_source[source[0]] = rows
                phase1_done += 1
                print(f'[A {phase1_done}/{len(sources)}] {record["source"]:<16} '
                      f'rows={record["probes"][0]["rows"]:>4} '
                      f'{"CAP " if record["probes"][0]["capped"] else "    "}'
                      f'{"err" if not record["probes"][0]["ok"] else ""}', flush=True)
        except concurrent.futures.TimeoutError:
            pass

        unfinished = [futures[f] for f in futures if not f.done()]
        for source in unfinished:
            records[source[0]] = error_record(source, "not reached: audit deadline (archive.org too slow)")
            rows_by_source[source[0]] = []

        # ---- Phase 2: narrow fallback probe only where Probe A was weak/failed ----
        pending_b = []
        for source in sources:
            record = records[source[0]]
            p = record["probes"][0]
            if p["capped"]:
                continue
            if not p["ok"] or p["rows"] < WEAK_THRESHOLD:
                if remaining() > PROBE_RESERVE:
                    pending_b.append(source)
                else:
                    err = record["probes"][0]["error"]
                    record["probes"][0]["error"] = (err + " | " if err else "") + "probe B skipped: audit deadline"

        b_futures = {pool.submit(probe_b, records[s[0]], rows_by_source[s[0]]): s for s in pending_b}
        b_done = 0
        try:
            for future in concurrent.futures.as_completed(b_futures, timeout=max(remaining() + PROBE_RESERVE, 60)):
                source = b_futures[future]
                try:
                    rows_b = future.result()
                    rows_by_source[source[0]].extend(rows_b)
                except Exception as exc:
                    records[source[0]]["probes"][-1]["error"] = f"{type(exc).__name__}: {exc}"
                b_done += 1
                rec = records[source[0]]
                print(f'[B {b_done}/{len(pending_b)}] {rec["source"]:<16} '
                      f'rows={rec["probes"][-1]["rows"]:>4} '
                      f'{"CAP " if rec["probes"][-1]["capped"] else "    "}'
                      f'{"err" if not rec["probes"][-1]["ok"] else ""}', flush=True)
        except concurrent.futures.TimeoutError:
            pass

        b_unfinished = [b_futures[f] for f in b_futures if not f.done()]
        for source in b_unfinished:
            records[source[0]]["probes"][-1]["error"] = "not completed: audit deadline (archive.org too slow)"

        for f in list(futures) + list(b_futures):
            f.cancel()

    if unfinished or b_unfinished:
        deadline_note = (
            f"Audit hit its {deadline}s deadline: {len(unfinished)} source(s) not reached, "
            f"{len(b_unfinished)} fallback probe(s) unfinished. Marked ERROR above; rerun on a "
            "healthier archive.org day for complete coverage."
        )
        print("\n" + deadline_note, flush=True)

    results = [finalize(records[s[0]], rows_by_source[s[0]]) for s in sources]
    total_requests = sum(r["requests"] for r in results)

    out_dir = Path(args.out)
    write_reports(results, out_dir, total_requests, deadline_note)

    print(f"\nCDX metadata requests made: {total_requests}", flush=True)
    print(f"Reports written to: {out_dir}", flush=True)
    print("source-audit.csv / source-audit.json / source-audit.md", flush=True)


if __name__ == "__main__":
    main()
