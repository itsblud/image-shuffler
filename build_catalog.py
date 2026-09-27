#!/usr/bin/env python3
from __future__ import annotations

import concurrent.futures
import json
import random
import re
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CATALOG_DIR = ROOT / "catalog"
REGION_DIR = CATALOG_DIR / "regions"
MANIFEST_FILE = CATALOG_DIR / "manifest.json"

USER_AGENT = "Shuffler/2.2 (private archival image browser)"
MIN_ARCHIVE_BYTES = 18_000

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

WAYBACK_SOURCES = [
    ("BlackPlanet", "blackplanet.com", 1999, "north-america"),
    ("BlackVoices", "blackvoices.com", 1995, "north-america"),
    ("Okayplayer", "okayplayer.com", 1999, "north-america"),
    ("AllHipHop", "allhiphop.com", 1998, "north-america"),
    ("GhanaWeb", "ghanaweb.com", 1999, "africa"),
    ("Nairaland", "nairaland.com", 2005, "africa"),
    ("Rediff", "rediff.com", 1996, "south-asia"),
    ("Sify", "sify.com", 1998, "south-asia"),
    ("Sulekha", "sulekha.com", 1998, "south-asia"),
    ("Indiatimes", "indiatimes.com", 1996, "south-asia"),
    ("Cyworld", "cyworld.com", 1999, "east-asia"),
    ("Mixi", "mixi.jp", 2004, "east-asia"),
    ("Xiaonei", "xiaonei.com", 2005, "east-asia"),
    ("51.com", "51.com", 2005, "east-asia"),
    ("QQ", "qq.com", 1999, "east-asia"),
    ("Fotolog", "fotolog.com", 2002, "latin-america-caribbean"),
    ("MiGente", "migente.com", 2000, "latin-america-caribbean"),
    ("Maktoob", "maktoob.com", 1998, "mena"),
    ("Jeeran", "jeeran.com", 2000, "mena"),
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
    ("China", "east-asia"),
    ("Japan", "east-asia"),
    ("Ghana", "africa"),
    ("Nigeria", "africa"),
    ("South Africa", "africa"),
    ("Egypt", "mena"),
    ("Iran", "mena"),
    ("Brazil", "latin-america-caribbean"),
    ("Mexico", "latin-america-caribbean"),
    ("Dominican Republic", "latin-america-caribbean"),
    ("Jamaica", "latin-america-caribbean"),
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
    delay = 1.2
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"},
            )
            with urllib.request.urlopen(req, timeout=14) as response:
                return json.loads(response.read().decode("utf-8", "replace"))
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(delay)
            delay *= 2

def infer_tags(text: str):
    return sorted({tag for tag, rx in TAG_PATTERNS if rx.search(text)})

def canonical_name(value: str):
    try:
        name = urllib.parse.unquote(
            urllib.parse.urlparse(value).path.rsplit("/", 1)[-1]
        ).lower()
    except Exception:
        name = str(value).lower()

    name = re.sub(r"\.(?:jpe?g|png|webp)$", "", name, flags=re.I)
    name = re.sub(
        r"(?:^|[_-])(thumb|thumbnail|small|medium|large|orig|original|preview|full)(?:$|[_-])",
        "_", name, flags=re.I,
    )
    name = re.sub(r"[_-]\d{2,4}x\d{2,4}(?=$|[_-])", "_", name)
    name = re.sub(r"[_-](?:sm|md|lg|xl)(?=$|[_-])", "_", name, flags=re.I)
    name = re.sub(r"[_\-\s]+", " ", name).strip()

    if len(name) < 8 or name in {
        "image", "photo", "picture", "pic", "img",
        "jpeg", "jpg", "dsc", "scan", "untitled"
    }:
        return ""
    return name

def normalize_existing_item(item):
    if not isinstance(item, dict):
        return None

    archive = item.get("archive")
    if not isinstance(archive, str) or not archive.startswith(
        ("https://web.archive.org/", "https://upload.wikimedia.org/")
    ):
        return None

    try:
        year = int(item.get("year"))
    except Exception:
        return None

    if not 1994 <= year <= 2008:
        return None

    original = str(item.get("original") or archive)
    digest = str(item.get("digest") or ("legacy-url:" + archive))
    tags = item.get("tags")
    if not isinstance(tags, list):
        tags = infer_tags(original)

    region = str(item.get("region") or "global")
    if region not in REGIONS:
        region = "global"

    return {
        "year": year,
        "source": str(item.get("source") or "Existing catalog"),
        "region": region,
        "country": item.get("country"),
        "digest": digest,
        "visualKey": str(item.get("visualKey") or canonical_name(original)),
        "tags": tags,
        "original": original,
        "archive": archive,
    }

def load_existing_v1_catalog():
    path = ROOT / "catalog.json"
    if not path.exists():
        print("existing v1 catalog imported: 0 (catalog.json not found)")
        return []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print("existing catalog read failed:", exc)
        return []

    if not isinstance(data, list):
        return []

    out = []
    for raw in data:
        item = normalize_existing_item(raw)
        if item:
            out.append(item)

    print("existing v1 catalog imported:", len(out))
    return out

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
        ("limit", "500"),
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

        if not 1994 <= year <= 2008:
            continue
        if not JPEG.search(original):
            continue
        if BAD_ASSET.search(original):
            continue
        if mime != "image/jpeg":
            continue
        if size and size < MIN_ARCHIVE_BYTES:
            continue
        if not digest:
            continue

        out.append({
            "year": year,
            "source": source,
            "region": region,
            "country": None,
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
        "gcmlimit": "200",
        "prop": "imageinfo|categories",
        "iiprop": "url|size|mime|sha1",
        "cllimit": "200",
        "clshow": "!hidden",
        "format": "json",
        "formatversion": "2",
    }
    url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)

    try:
        data = fetch_json(url)
    except Exception as exc:
        print(f"skip Commons {country} {year}: {exc}")
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
        if min(width, height) < 200 or max(width, height) < 300:
            continue
        if size and size < MIN_ARCHIVE_BYTES:
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

def dedupe_and_balance(rows):
    random.shuffle(rows)

    seen_digest = set()
    seen_visual = set()
    seen_original = set()

    source_counts = defaultdict(int)
    region_counts = defaultdict(int)
    kept = []

    SOURCE_CAP = 600
    REGION_CAP = 3500

    for item in rows:
        region = item.get("region")
        if region not in REGIONS:
            region = "global"
            item["region"] = "global"

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
        if region_counts[region] >= REGION_CAP:
            continue

        if digest:
            seen_digest.add(digest)
        if visual:
            seen_visual.add(visual)
        if original:
            seen_original.add(original)

        source_counts[source] += 1
        region_counts[region] += 1
        kept.append(item)

    return kept, dict(source_counts), dict(region_counts)

def main():
    REGION_DIR.mkdir(parents=True, exist_ok=True)

    rows = []
    rows.extend(load_existing_v1_catalog())

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
                print("Wayback worker error:", exc)

    commons_jobs = [
        (country, region, year)
        for country, region in COMMONS_COUNTRIES
        for year in range(1994, 2009)
    ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(query_commons, *job) for job in commons_jobs]
        for future in concurrent.futures.as_completed(futures):
            try:
                rows.extend(future.result())
            except Exception as exc:
                print("Commons worker error:", exc)

    print("raw candidates:", len(rows))

    kept, source_counts, region_counts = dedupe_and_balance(rows)

    print("after cheap dedupe:", len(kept))
    print("source counts:", json.dumps(source_counts, sort_keys=True))
    print("region counts:", json.dumps(region_counts, sort_keys=True))

    shards = {region: [] for region in REGIONS}

    for item in kept:
        shards[item.get("region", "global")].append(item)

    for region in REGIONS:
        random.shuffle(shards[region])
        (REGION_DIR / f"{region}.json").write_text(
            json.dumps(shards[region], separators=(",", ":"), ensure_ascii=False),
            encoding="utf-8",
        )

    total = sum(len(items) for items in shards.values())

    if total == 0:
        raise RuntimeError(
            "No usable images were found at all. Existing catalog was not available "
            "and every source query failed."
        )

    manifest = {
        "version": str(int(time.time())),
        "generated": int(time.time()),
        "regions": {
            region: {
                "file": f"catalog/regions/{region}.json",
                "count": len(shards[region]),
            }
            for region in REGIONS
        },
        "total": total,
    }

    MANIFEST_FILE.write_text(
        json.dumps(manifest, separators=(",", ":")),
        encoding="utf-8",
    )

    print("final total:", total)
    print(
        "final regions:",
        json.dumps({region: len(shards[region]) for region in REGIONS}, sort_keys=True),
    )

if __name__ == "__main__":
    main()
