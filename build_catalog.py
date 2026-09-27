#!/usr/bin/env python3
"""
Shuffler v2 catalog builder.

Design goals:
- sources are modular
- expensive visual fingerprints are cached by archive digest
- exact and near-duplicate images are removed at build time
- output is sharded by region
- the browser never needs to process the whole image world at once
"""

from __future__ import annotations

import concurrent.futures
import io
import json
import random
import re
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent
CATALOG_DIR = ROOT / "catalog"
REGION_DIR = CATALOG_DIR / "regions"
CACHE_FILE = CATALOG_DIR / "fingerprint-cache.json"
MANIFEST_FILE = CATALOG_DIR / "manifest.json"

MIN_SHORT_SIDE = 200
MIN_LONG_SIDE = 300
USER_AGENT = "Shuffler/2.0 (private archival image browser)"

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

# These assignments are intentionally pragmatic, not museum-grade metadata.
# Global image-hosting sites stay "global" instead of pretending we know where
# every user/photo came from.
WAYBACK_SOURCES = [
    # Black / North American web culture
    ("BlackPlanet", "blackplanet.com", 1999, "north-america"),
    ("BlackVoices", "blackvoices.com", 1995, "north-america"),
    ("Okayplayer", "okayplayer.com", 1999, "north-america"),
    ("AllHipHop", "allhiphop.com", 1998, "north-america"),

    # Africa
    ("GhanaWeb", "ghanaweb.com", 1999, "africa"),
    ("Nairaland", "nairaland.com", 2005, "africa"),

    # South Asia
    ("Rediff", "rediff.com", 1996, "south-asia"),
    ("Sify", "sify.com", 1998, "south-asia"),
    ("Sulekha", "sulekha.com", 1998, "south-asia"),
    ("Indiatimes", "indiatimes.com", 1996, "south-asia"),

    # East Asia
    ("Cyworld", "cyworld.com", 1999, "east-asia"),
    ("Mixi", "mixi.jp", 2004, "east-asia"),
    ("Xiaonei", "xiaonei.com", 2005, "east-asia"),
    ("51.com", "51.com", 2005, "east-asia"),
    ("QQ", "qq.com", 1999, "east-asia"),

    # Latin America / Caribbean
    ("Fotolog", "fotolog.com", 2002, "latin-america-caribbean"),
    ("MiGente", "migente.com", 2000, "latin-america-caribbean"),

    # MENA
    ("Maktoob", "maktoob.com", 1998, "mena"),
    ("Jeeran", "jeeran.com", 2000, "mena"),

    # Global old-web / image-hosting pools
    ("GeoCities", "geocities.com", 1994, "global"),
    ("Photo.net", "photo.net", 1994, "global"),
    ("Webshots", "webshots.com", 1995, "global"),
    ("Tripod", "tripod.com", 1995, "global"),
    ("Angelfire", "angelfire.com", 1996, "global"),
    ("Fotki", "fotki.com", 1998, "global"),
    ("PictureTrail", "picturetrail.com", 1998, "global"),
    ("PBase", "pbase.com", 1999, "global"),
    ("SmugMug", "smugmug.com", 2002, "global"),
    ("Photobucket", "photobucket.com", 2003, "global"),
    ("ImageShack", "imageshack.us", 2003, "global"),
    ("TinyPic", "tinypic.com", 2004, "global"),
    ("Flickr", "static.flickr.com", 2004, "global"),
    ("Zooomr", "zooomr.com", 2005, "global"),
    ("Orkut", "orkut.com", 2004, "global"),
    ("Hi5", "hi5.com", 2003, "global"),
]

COMMONS_COUNTRIES = [
    ("India", "south-asia"),
    ("Pakistan", "south-asia"),
    ("Bangladesh", "south-asia"),
    ("Sri Lanka", "south-asia"),
    ("China", "east-asia"),
    ("Japan", "east-asia"),
    ("South Korea", "east-asia"),
    ("Ghana", "africa"),
    ("Nigeria", "africa"),
    ("Kenya", "africa"),
    ("South Africa", "africa"),
    ("Egypt", "mena"),
    ("Iran", "mena"),
    ("Lebanon", "mena"),
    ("Morocco", "mena"),
    ("Brazil", "latin-america-caribbean"),
    ("Mexico", "latin-america-caribbean"),
    ("Dominican Republic", "latin-america-caribbean"),
    ("Jamaica", "latin-america-caribbean"),
    ("United States", "north-america"),
    ("Canada", "north-america"),
    ("France", "europe"),
    ("Germany", "europe"),
    ("United Kingdom", "europe"),
    ("Italy", "europe"),
]

BAD_ASSET = re.compile(
    r"(avatar|banner|button|icon|logo|pixel|spacer|tracker|counter|"
    r"smiley|emoticon|sprite|thumb|thumbnail|favicon|advert|adserver|"
    r"badge|toolbar|header|footer|nav|menu)",
    re.I,
)
JPEG = re.compile(r"\.(?:jpe?g)(?:[?#].*)?$", re.I)

TAG_PATTERNS = [
    ("people", re.compile(r"(person|people|family|friends?|portrait|wedding|party|birthday|child|children|man|woman|boy|girl)", re.I)),
    ("signs", re.compile(r"(sign|signage|poster|billboard|advert|advertising|graphic|lettering|sticker|shopfront|storefront)", re.I)),
    ("fashion", re.compile(r"(fashion|clothing|clothes|dress|shirt|jacket|style|outfit|uniform)", re.I)),
    ("vehicles", re.compile(r"(car|cars|automobile|truck|bus|train|tram|motorcycle|bike|bicycle|airplane|plane|vehicle|taxi)", re.I)),
    ("interiors", re.compile(r"(interior|living room|bedroom|kitchen|home|household|domestic)", re.I)),
    ("objects", re.compile(r"(object|product|packaging|package|toy|food|bottle|box|store|shop|market)", re.I)),
    ("religion", re.compile(r"(religion|religious|church|mosque|temple|shrine|ritual|jesus|christ|allah|god|festival)", re.I)),
    ("animals", re.compile(r"(animal|dog|cat|bird|horse|pet|wildlife|zoo)", re.I)),
    ("architecture", re.compile(r"(architecture|architectural|building|buildings|cathedral|castle|skyscraper|bridge|monument|facade|façade|tower)", re.I)),
    ("landscape", re.compile(r"(landscape|scenery|scenic|mountain|sunset|sunrise|waterfall|countryside|seascape|forest|beach|river|lake|nature)", re.I)),
    ("flowers", re.compile(r"(flower|flowers|floral|blossom|rose|tulip|orchid|botanical|plant|plants|garden)", re.I)),
]

def fetch_json(url: str, retries: int = 2):
    delay = 1.5
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"},
            )
            with urllib.request.urlopen(req, timeout=18) as response:
                return json.loads(response.read().decode("utf-8", "replace"))
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(delay)
            delay *= 2

def fetch_bytes(url: str, max_bytes: int = 12_000_000):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "image/*,*/*"},
    )
    with urllib.request.urlopen(req, timeout=12) as response:
        data = response.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError("image too large")
        return data

def infer_tags(text: str):
    return sorted({tag for tag, rx in TAG_PATTERNS if rx.search(text)})

def canonical_name(value: str):
    try:
        name = urllib.parse.unquote(urllib.parse.urlparse(value).path.rsplit("/", 1)[-1]).lower()
    except Exception:
        name = str(value).lower()

    name = re.sub(r"\.(?:jpe?g|png)$", "", name, flags=re.I)
    name = re.sub(
        r"(?:^|[_-])(thumb|thumbnail|small|medium|large|orig|original|preview)(?:$|[_-])",
        "_",
        name,
        flags=re.I,
    )
    name = re.sub(r"[_-]\d{2,4}x\d{2,4}(?=$|[_-])", "_", name)
    name = re.sub(r"[_\-\s]+", " ", name).strip()

    if len(name) < 8 or name in {
        "image","photo","picture","pic","img","jpeg","jpg","dsc","scan","untitled"
    }:
        return ""
    return name

def query_wayback(source, domain, start_year, end_year, region):
    params = [
        ("url", domain),
        ("matchType", "domain"),
        ("output", "json"),
        ("fl", "timestamp,original,mimetype,statuscode,digest,length"),
        ("from", f"{start_year:04d}0101"),
        ("to", f"{end_year:04d}1231"),
        ("filter", "statuscode:200"),
        ("filter", "mimetype:image/jpeg"),
        ("collapse", "digest"),
        ("limit", "700"),
    ]
    url = "https://web.archive.org/cdx/search/cdx?" + urllib.parse.urlencode(params)

    try:
        rows = fetch_json(url)
    except Exception as exc:
        print(f"skip {source} {start_year}-{end_year}: {exc}")
        return []

    out = []
    for row in rows[1:] if rows and len(rows) > 1 else []:
        if len(row) < 6:
            continue

        ts, original, mime, status, digest, length = row[:6]

        try:
            year = int(ts[:4])
            size = int(length or 0)
        except Exception:
            continue

        if not (1994 <= year <= 2008):
            continue
        if not JPEG.search(original):
            continue
        if BAD_ASSET.search(original):
            continue
        if mime != "image/jpeg":
            continue
        if size and size < 18_000:
            continue
        if not digest:
            continue

        out.append({
            "year": year,
            "source": source,
            "region": region,
            "digest": "wayback:" + digest,
            "visualKey": canonical_name(original),
            "tags": infer_tags(urllib.parse.unquote(original)),
            "original": original,
            "archive": f"https://web.archive.org/web/{ts}id_/{original}",
        })
    return out

def query_commons(country, region, year):
    params = {
        "action": "query",
        "generator": "categorymembers",
        "gcmtitle": f"Category:{year} photographs of {country}",
        "gcmtype": "file",
        "gcmlimit": "max",
        "prop": "imageinfo|categories",
        "iiprop": "url|size|mime|sha1",
        "cllimit": "max",
        "clshow": "!hidden",
        "format": "json",
        "formatversion": "2",
    }
    url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)

    try:
        data = fetch_json(url)
    except Exception:
        return []

    out = []
    for page in data.get("query", {}).get("pages", []):
        info_list = page.get("imageinfo") or []
        if not info_list:
            continue

        info = info_list[0]
        image_url = info.get("url", "")
        sha1 = info.get("sha1", "")
        width = int(info.get("width") or 0)
        height = int(info.get("height") or 0)
        size = int(info.get("size") or 0)

        if info.get("mime") != "image/jpeg":
            continue
        if not image_url.startswith("https://upload.wikimedia.org/"):
            continue
        if min(width, height) < MIN_SHORT_SIDE or max(width, height) < MIN_LONG_SIDE:
            continue
        if size and size < 18_000:
            continue
        if not sha1:
            continue

        title = page.get("title", "")
        categories = " ".join(c.get("title", "") for c in page.get("categories", []))
        out.append({
            "year": year,
            "source": "Wikimedia Commons",
            "region": region,
            "country": country,
            "digest": "commons:" + sha1,
            "visualKey": canonical_name(title),
            "tags": infer_tags(f"{title} {categories}"),
            "original": title,
            "archive": image_url,
            "width": width,
            "height": height,
        })

    return out

def load_cache():
    if not CACHE_FILE.exists():
        return {}
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def dhash_and_dimensions(item, cache):
    digest = item["digest"]
    cached = cache.get(digest)
    if cached and cached.get("phash"):
        result = dict(item)
        result.update({
            "phash": cached["phash"],
            "width": cached.get("width", item.get("width", 0)),
            "height": cached.get("height", item.get("height", 0)),
        })
        return result

    try:
        data = fetch_bytes(item["archive"])
        with Image.open(io.BytesIO(data)) as im:
            im = im.convert("L")
            width, height = im.size

            if min(width, height) < MIN_SHORT_SIDE or max(width, height) < MIN_LONG_SIDE:
                return None

            thumb = im.resize((9, 8), Image.Resampling.LANCZOS)
            pixels = list(thumb.getdata())

            bits = 0
            for y in range(8):
                for x in range(8):
                    bits = (bits << 1) | int(
                        pixels[y * 9 + x] > pixels[y * 9 + x + 1]
                    )

            phash = f"{bits:016x}"
            cache[digest] = {"phash": phash, "width": width, "height": height}

            result = dict(item)
            result.update({"phash": phash, "width": width, "height": height})
            return result
    except Exception as exc:
        print("fingerprint skip:", item.get("source"), exc)
        return None

def hamming(a: str, b: str):
    return (int(a, 16) ^ int(b, 16)).bit_count()

def main():
    REGION_DIR.mkdir(parents=True, exist_ok=True)
    cache = load_cache()

    rows = []

    wayback_jobs = []
    for source, domain, born, region in WAYBACK_SOURCES:
        y = max(1994, born)
        while y <= 2008:
            wayback_jobs.append((source, domain, y, min(y + 2, 2008), region))
            y += 3

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(query_wayback, *job) for job in wayback_jobs]
        for future in concurrent.futures.as_completed(futures):
            try:
                rows.extend(future.result())
            except Exception as exc:
                print("Wayback worker:", exc)

    commons_jobs = [
        (country, region, year)
        for country, region in COMMONS_COUNTRIES
        for year in range(1994, 2009)
    ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(query_commons, *job) for job in commons_jobs]
        for future in concurrent.futures.as_completed(futures):
            try:
                rows.extend(future.result())
            except Exception as exc:
                print("Commons worker:", exc)

    # Cheap exact / normalized-name dedupe first.
    random.shuffle(rows)
    seen_digest = set()
    seen_name = set()
    preliminary = []
    per_region = defaultdict(int)
    per_source = defaultdict(int)

    for item in rows:
        digest = item["digest"]
        visual_key = item.get("visualKey") or ""
        source = item["source"]
        region = item.get("region", "global")

        if digest in seen_digest:
            continue
        if visual_key and visual_key in seen_name:
            continue
        if per_source[source] >= 250:
            continue
        if per_region[region] >= 700:
            continue

        seen_digest.add(digest)
        if visual_key:
            seen_name.add(visual_key)

        per_source[source] += 1
        per_region[region] += 1
        preliminary.append(item)

    print("preliminary candidates:", len(preliminary))

    # Expensive work is cached by archive digest. On later builds, unchanged
    # images skip the download/fingerprint stage.
    fingerprinted = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(dhash_and_dimensions, item, cache) for item in preliminary]
        for future in concurrent.futures.as_completed(futures):
            try:
                result = future.result()
                if result:
                    fingerprinted.append(result)
            except Exception as exc:
                print("fingerprint worker:", exc)

    # Global perceptual duplicate suppression.
    random.shuffle(fingerprinted)
    kept = []
    kept_hashes = []

    for item in fingerprinted:
        phash = item.get("phash")
        if phash and any(hamming(phash, existing) <= 5 for existing in kept_hashes):
            continue
        if phash:
            kept_hashes.append(phash)
        kept.append(item)

    # Final region shards.
    shards = {region: [] for region in REGIONS}
    for item in kept:
        region = item.get("region", "global")
        if region not in shards:
            region = "global"
        shards[region].append(item)

    for region in REGIONS:
        random.shuffle(shards[region])
        path = REGION_DIR / f"{region}.json"
        path.write_text(
            json.dumps(shards[region], separators=(",", ":"), ensure_ascii=False),
            encoding="utf-8",
        )

    cache_payload = {
        digest: value
        for digest, value in cache.items()
        if isinstance(value, dict) and value.get("phash")
    }
    CACHE_FILE.write_text(
        json.dumps(cache_payload, separators=(",", ":")),
        encoding="utf-8",
    )

    version = str(int(time.time()))
    manifest = {
        "version": version,
        "generated": int(time.time()),
        "regions": {
            region: {
                "file": f"catalog/regions/{region}.json",
                "count": len(shards[region]),
            }
            for region in REGIONS
        },
        "total": sum(len(items) for items in shards.values()),
    }

    if manifest["total"] < 80:
        raise RuntimeError("Too few usable images; catalog was not replaced.")

    MANIFEST_FILE.write_text(
        json.dumps(manifest, separators=(",", ":")),
        encoding="utf-8",
    )

    print("final total:", manifest["total"])
    print("regions:", json.dumps({r: len(shards[r]) for r in REGIONS}, sort_keys=True))

if __name__ == "__main__":
    main()
