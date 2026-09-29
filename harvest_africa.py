#!/usr/bin/env python3
"""Targeted Africa-only harvest: queries GhanaWeb + Nairaland over 1994-2016
with gentle pacing, then merges results into the existing catalog files
without touching other regions' data."""
from __future__ import annotations

import json
import argparse
import random
import time
from collections import defaultdict
from pathlib import Path

import build_catalog as bc

AFRICA_SOURCES = [
    ("GhanaWeb", "ghanaweb.com", 1999, 2016),
    ("Nairaland", "nairaland.com", 2005, 2016),
]

REGION_CAP = 6000
SOURCE_CAP = 3000
CHECKPOINT_DIR = bc.ROOT / 'africa-harvest-results'

def harvest(source, domain, born, end_year):
    start = max(1994, born)
    out = []
    failed = []
    consecutive_failures = 0
    for y in reversed(range(start, end_year + 1, 3)):
        chunk_end = min(y + 2, end_year)
        ok = False
        delay = 5.0
        for attempt in range(2):
            try:
                rows = bc.query_wayback_range(
                    source, domain, y, chunk_end, "africa",
                    limit=1000, timeout=30, retries=1,
                )
                out.extend(rows)
                CHECKPOINT_DIR.mkdir(exist_ok=True)
                (CHECKPOINT_DIR / f'{domain}-{y}-{chunk_end}.json').write_text(
                    json.dumps(rows, ensure_ascii=False), encoding='utf-8'
                )
                print(f"{source} {y}-{chunk_end}: {len(rows)} rows")
                ok = True
                break
            except Exception as exc:
                print(f"{source} {y}-{chunk_end} attempt {attempt + 1} failed: {exc}")
                if attempt < 1:
                    time.sleep(delay)
                    delay *= 2
        if not ok:
            failed.append((source, y, chunk_end))
            consecutive_failures += 1
            if consecutive_failures >= 2:
                print(f'{source}: stopping after two consecutive failed date batches; earlier periods not queried.')
                break
        else:
            consecutive_failures = 0
        time.sleep(3.0)
    return out, failed

def main(sources=None):
    random.seed()

    existing_path = bc.REGION_DIR / 'africa.json'
    existing_africa = json.loads(existing_path.read_text(encoding='utf-8')) if existing_path.exists() else []
    new_rows = []
    failed = []
    for source, domain, born, end_year in (sources if sources is not None else AFRICA_SOURCES):
        rows, f = harvest(source, domain, born, end_year)
        new_rows.extend(rows)
        failed.extend(f)
        time.sleep(5.0)

    print("raw africa candidates:", len(new_rows))
    if failed:
        print("FAILED CHUNKS:", failed)
    CHECKPOINT_DIR.mkdir(exist_ok=True)
    (CHECKPOINT_DIR / 'summary.json').write_text(json.dumps({
        'raw_candidates': len(new_rows), 'failed_chunks': failed,
        'existing_entries': len(existing_africa), 'finished': int(time.time())
    }, indent=2), encoding='utf-8')
    if not new_rows:
        print('No new candidates; existing catalog preserved unchanged.')
        return

    # Load existing regional shards; other regions pass through untouched.
    shards = {}
    for region in bc.REGIONS:
        path = bc.REGION_DIR / f"{region}.json"
        if path.exists() and region != "africa":
            shards[region] = json.loads(path.read_text(encoding="utf-8"))
        else:
            shards[region] = []

    # Global dedupe sets from all non-africa entries.
    seen_digest = set()
    seen_visual = set()
    seen_original = set()
    for region in bc.REGIONS:
        if region == "africa":
            continue
        for item in shards[region]:
            if item.get("digest"):
                seen_digest.add(str(item["digest"]))
            if item.get("visualKey"):
                seen_visual.add(str(item["visualKey"]))
            if item.get("original"):
                seen_original.add(str(item["original"]))

    random.shuffle(new_rows)
    new_rows = existing_africa + new_rows
    source_counts = defaultdict(int)
    africa = []
    for item in new_rows:
        digest = str(item.get("digest") or "")
        visual = str(item.get("visualKey") or "")
        original = str(item.get("original") or "")
        source = str(item.get("source") or "Unknown")

        if digest and digest in seen_digest:
            continue
        if visual and visual in seen_visual:
            continue
        if original and original in seen_original:
            continue
        if source_counts[source] >= SOURCE_CAP:
            continue
        if len(africa) >= REGION_CAP:
            break

        if digest:
            seen_digest.add(digest)
        if visual:
            seen_visual.add(visual)
        if original:
            seen_original.add(original)

        source_counts[source] += 1
        africa.append(item)

    print("africa kept:", len(africa))
    print("africa source counts:", json.dumps(dict(source_counts), sort_keys=True))
    year_dist = defaultdict(int)
    for item in africa:
        year_dist[item["year"]] += 1
    print("africa year distribution:", json.dumps(dict(sorted(year_dist.items()))))

    shards["africa"] = africa

    (bc.REGION_DIR / "africa.json").write_text(
        json.dumps(africa, separators=(",", ":"), ensure_ascii=False),
        encoding="utf-8",
    )

    flat = []
    for region in bc.REGIONS:
        flat.extend(shards[region])
    (bc.ROOT / "catalog.json").write_text(
        json.dumps(flat, separators=(",", ":"), ensure_ascii=False),
        encoding="utf-8",
    )

    total = sum(len(shards[r]) for r in bc.REGIONS)
    manifest = {
        "version": str(int(time.time())),
        "generated": int(time.time()),
        "regions": {
            region: {
                "file": f"catalog/regions/{region}.json",
                "count": len(shards[region]),
            }
            for region in bc.REGIONS
        },
        "total": total,
    }
    bc.MANIFEST_FILE.write_text(
        json.dumps(manifest, separators=(",", ":")),
        encoding="utf-8",
    )

    print("final total:", total)
    print("final regions:", json.dumps({r: len(shards[r]) for r in bc.REGIONS}, sort_keys=True))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', choices=[s[0] for s in AFRICA_SOURCES])
    args = parser.parse_args()
    main([s for s in AFRICA_SOURCES if not args.source or s[0] == args.source])
