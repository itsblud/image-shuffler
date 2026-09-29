#!/usr/bin/env python3
"""Resumable yearly sampling of personal homepages and Rave.ca (through 2008).
Run without --merge to collect checkpoints only. Failed requests are retried
on the next run; successful year samples are reused. This is not exhaustive.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import random
import subprocess
import time

import build_catalog as bc

NAMES = {'GeoCities', 'Angelfire', 'Tripod', 'FortuneCity', 'Rave.ca'}
SOURCES = [s for s in bc.WAYBACK_SOURCES if s[0] in NAMES]
CACHE = bc.ROOT / 'personal-rave-harvest'


def curl_json(url, retries=1, timeout=20):
    """Use the system HTTPS trust store; TLS verification stays enabled."""
    result = subprocess.run(['curl', '--fail', '--silent', '--show-error',
        '--location', '--max-time', str(timeout), '--user-agent', bc.USER_AGENT,
        url], capture_output=True, text=True, timeout=timeout + 5, check=True)
    return json.loads(result.stdout)


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    temp.replace(path)


def merge(shards, candidates, per_source=500):
    """Reserve bounded space for novel entries; preserve unaffected regions.
    Keep existing limits of 3,000/source and 6,000/region. Existing entries
    displaced by those limits remain recoverable in the pre-merge backup.
    """
    existing = [item for items in shards.values() for item in items]
    digests = {i['digest'] for i in existing}
    urls = {i['original'] for i in existing}
    visuals = {i['visualKey'] for i in existing if i.get('visualKey')}
    fresh = []
    counts = Counter()
    sampled = Counter()
    sampled_digests = set()
    candidates = list(candidates)
    random.Random(29).shuffle(candidates)
    for item in candidates:
        if item['digest'] in sampled_digests or sampled[item['source']] >= per_source:
            continue
        sampled_digests.add(item['digest'])
        sampled[item['source']] += 1
        if item['digest'] in digests or item['original'] in urls:
            continue
        if item.get('visualKey') and item['visualKey'] in visuals:
            continue
        counts[item['source']] += 1
        digests.add(item['digest'])
        urls.add(item['original'])
        if item.get('visualKey'):
            visuals.add(item['visualKey'])
        fresh.append(item)
    result = {region: list(items) for region, items in shards.items()}
    for region in {i['region'] for i in fresh}:
        combined = [i for i in fresh if i['region'] == region] + shards[region]
        kept, source_counts = [], Counter()
        for item in combined:
            if len(kept) >= 6000:
                break
            if source_counts[item['source']] >= 3000:
                continue
            kept.append(item)
            source_counts[item['source']] += 1
        result[region] = kept
    return result, dict(counts)


def run(args):
    if args.transport == 'curl':
        bc.fetch_json = curl_json
    candidates, report = [], []
    for source, domain, born, region, end in SOURCES:
        if args.source and source not in args.source:
            continue
        failures = 0
        for year in range(min(end, args.to_year), max(born, args.from_year) - 1, -1):
            cache = CACHE / f'{domain}-{year}-{args.limit}-v1.json'
            try:
                if cache.exists():
                    rows = json.loads(cache.read_text())
                    state = 'cached'
                else:
                    rows = bc.query_wayback_range(source, domain, year, year, region,
                        limit=args.limit, timeout=args.timeout, retries=1,
                        image_types=('jpeg', 'png', 'gif'))
                    save(cache, rows)
                    state = 'fetched'
                    time.sleep(args.delay)
                candidates.extend(rows)
                report.append({'source': source, 'year': year, 'status': state, 'candidates': len(rows)})
                print(f'{source} {year}: {len(rows)} candidates ({state})', flush=True)
                failures = 0
            except Exception as exc:
                report.append({'source': source, 'year': year, 'status': 'failed', 'error': str(exc)})
                print(f'{source} {year}: {exc}', flush=True)
                failures += 1
                if failures >= 2:
                    print('Deferring earlier years after two consecutive failures.', flush=True)
                    break
                time.sleep(args.delay)
    summary = {'queries': report, 'candidate_count': len(candidates), 'added': {},
               'note': 'Bounded yearly samples; archive captures date availability, not photo creation. Replay is not verified.'}
    if args.merge and candidates:
        shards = {r: json.loads((bc.REGION_DIR / f'{r}.json').read_text()) for r in bc.REGIONS}
        updated, added = merge(shards, candidates, args.per_source)
        summary['added'] = added
        if added:
            backup = CACHE / f'backup-{time.time_ns()}'
            for region, items in shards.items():
                save(backup / f'{region}.json', items)
            save(backup / 'manifest.json', json.loads(bc.MANIFEST_FILE.read_text()))
            for region in shards:
                if updated[region] != shards[region]:
                    save(bc.REGION_DIR / f'{region}.json', updated[region])
            flat = [i for r in bc.REGIONS for i in updated[r]]
            save(bc.ROOT / 'catalog.json', flat)
            now = int(time.time())
            save(bc.MANIFEST_FILE, {'version': str(now), 'generated': now,
                'regions': {r: {'file': f'catalog/regions/{r}.json', 'count': len(updated[r])} for r in bc.REGIONS},
                'total': len(flat)})
    save(CACHE / 'summary.json', summary)
    print(json.dumps({'candidates': len(candidates), 'added': summary['added']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', action='append', choices=sorted(NAMES))
    parser.add_argument('--from-year', type=int, default=1994)
    parser.add_argument('--to-year', type=int, default=2008)
    parser.add_argument('--limit', type=int, default=1000)
    parser.add_argument('--timeout', type=int, default=20)
    parser.add_argument('--delay', type=float, default=2)
    parser.add_argument('--per-source', type=int, default=500)
    parser.add_argument('--merge', action='store_true')
    parser.add_argument('--transport', choices=['urllib', 'curl'], default='urllib')
    args = parser.parse_args()
    if not 1994 <= args.from_year <= args.to_year <= 2008:
        parser.error('Years must be ordered and between 1994 and 2008.')
    if min(args.limit, args.timeout, args.per_source) <= 0 or args.delay < 0:
        parser.error('Limits and timeout must be positive; delay cannot be negative.')
    run(args)
