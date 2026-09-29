#!/usr/bin/env python3
"""
Shuffler catalog verifier

Takes the current regional catalog, drops Wikimedia Commons, dedupes by
archive digest, then replay-tests every candidate image URL against
web.archive.org (same way the browser loads them).

Only images that actually play are written back:
    catalog/regions/<region>.json
    catalog/manifest.json
    catalog.json             (flattened, legacy)

Read-only towards the archive in the metadata sense: each candidate URL is
fetched once (twice on transient failure) to confirm it is a real image.

Usage:
    python verify_catalog.py             # full verification
    python verify_catalog.py --limit 20  # smoke test the first N candidates
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import random
import time
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CATALOG_DIR = ROOT / "catalog"
REGION_DIR = CATALOG_DIR / "regions"
MANIFEST_FILE = CATALOG_DIR / "manifest.json"

USER_AGENT = "Shuffler-Verify/1.0 (catalog viability check)"
EXCLUDE_SOURCES = {"Wikimedia Commons"}
MIN_IMAGE_BYTES = 5_000
MAX_IMAGE_BYTES = 8_000_000
WORKERS = 2
TIMEOUT = 25
REQUEST_DELAY = (0.25, 0.6)
REFUSED_BACKOFFS = (10, 25, 50)

REGIONS = [
    "north-america",
    "latin-america-caribbean",
    "africa",
    "south-asia",
    "east-asia",
    "mena",
    "europe",
    "global",
]


def load_candidates():
    seen_digest = set()
    seen_archive = set()
    candidates = []
    dropped = Counter()

    for region in REGIONS:
        path = REGION_DIR / f"{region}.json"
        if not path.exists():
            continue
        try:
            items = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            items = []

        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("source") in EXCLUDE_SOURCES:
                dropped["excluded-source"] += 1
                continue
            digest = str(item.get("digest") or "")
            archive = str(item.get("archive") or "")
            if not archive.startswith("https://web.archive.org/"):
                dropped["bad-archive-url"] += 1
                continue
            if digest and digest in seen_digest:
                dropped["dup-digest"] += 1
                continue
            if archive in seen_archive:
                dropped["dup-archive-url"] += 1
                continue
            if digest:
                seen_digest.add(digest)
            seen_archive.add(archive)
            candidates.append(item)

    return candidates, dropped


def check(item):
    url = item["archive"]
    time.sleep(random.uniform(*REQUEST_DELAY))
    refused_retries = 0
    for attempt in range(2):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "image/*,*/*;q=0.1"},
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
                status = response.status
                ctype = (response.headers.get("Content-Type") or "").lower()
                body = response.read(MAX_IMAGE_BYTES + 1)
            if status != 200:
                last = f"http {status}"
            elif not ctype.startswith("image/"):
                last = f"content-type {ctype or 'unknown'}"
            elif len(body) < MIN_IMAGE_BYTES:
                last = f"too small ({len(body)} bytes)"
            elif len(body) > MAX_IMAGE_BYTES:
                last = f"too large (>{MAX_IMAGE_BYTES} bytes)"
            else:
                return True, ""
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
            if "Connection refused" in str(exc) and refused_retries < len(REFUSED_BACKOFFS):
                time.sleep(REFUSED_BACKOFFS[refused_retries])
                refused_retries += 1
                continue
            if attempt == 0:
                time.sleep(1.0)
    return False, last


def main():
    parser = argparse.ArgumentParser(description="Verify Shuffler catalog replay viability")
    parser.add_argument("--limit", type=int, default=0, help="only test the first N candidates")
    parser.add_argument("--dry-run", action="store_true", help="test but do not write catalog files")
    args = parser.parse_args()

    candidates, dropped = load_candidates()
    random.shuffle(candidates)
    if args.limit:
        candidates = candidates[: args.limit]

    print(f"candidates after cleaning: {len(candidates)}  (dropped: {dict(dropped)})")
    print(f"replay-testing {len(candidates)} urls, {WORKERS} at a time...\n")

    results = {}
    failures = []
    done = 0
    t0 = time.time()

    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(check, item): item for item in candidates}
        for future in concurrent.futures.as_completed(futures):
            item = futures[future]
            try:
                ok, reason = future.result()
            except Exception as exc:
                ok, reason = False, f"checker error: {type(exc).__name__}: {exc}"
            results[item["archive"]] = ok
            done += 1
            if not ok:
                failures.append((item["source"], item["archive"], reason))
            print(f"[{done}/{len(candidates)}] {'ok ' if ok else 'FAIL'} {item['source']:<14} "
                  f"{item['archive'][-70:]}" + ("" if ok else f"  -> {reason[:60]}"), flush=True)

    survivors = [item for item in candidates if results[item["archive"]]]
    elapsed = time.time() - t0

    print(f"\ntested {len(candidates)} in {elapsed:.0f}s")
    print(f"playable survivors: {len(survivors)}")
    print("survivors by source:", dict(Counter(i["source"] for i in survivors).most_common()))
    print("survivors by region:", dict(Counter(i.get("region", "global") for i in survivors).most_common()))
    print(f"failures: {len(failures)}")
    for source, url, reason in failures[:20]:
        print(f"  {source:<14} {reason[:50]:<50} {url[-60:]}")

    if args.dry_run:
        print("\n--dry-run: no files written")
        return

    shards = {region: [] for region in REGIONS}
    for item in survivors:
        region = item.get("region") if item.get("region") in REGIONS else "global"
        shards[region].append(item)

    version = str(int(time.time()))
    for region in REGIONS:
        (REGION_DIR / f"{region}.json").write_text(
            json.dumps(shards[region], separators=(",", ":"), ensure_ascii=False),
            encoding="utf-8",
        )

    MANIFEST_FILE.write_text(
        json.dumps(
            {
                "version": version,
                "generated": int(time.time()),
                "regions": {
                    region: {
                        "file": f"catalog/regions/{region}.json",
                        "count": len(shards[region]),
                    }
                    for region in REGIONS
                },
                "total": len(survivors),
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )

    flat = []
    for region in REGIONS:
        flat.extend(shards[region])
    (ROOT / "catalog.json").write_text(
        json.dumps(flat, separators=(",", ":"), ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"\nwrote catalog/regions/*.json, catalog/manifest.json (version {version}), catalog.json")
    print("total in manifest:", len(survivors))


if __name__ == "__main__":
    main()
