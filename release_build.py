#!/usr/bin/env python3
"""Freeze a version's app AND catalog. Never overwrite an existing release.
Usage: python release_build.py 2.9 --notes 'Release notes' [--source PATH]
Commit generated builds/, downloads/, releases.json and releases.html together.
"""
import argparse
import hashlib
import html
import json
from pathlib import Path
import re
import shutil
import tempfile
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parent
FILES = ['index.html', 'app.js', 'styles.css', 'pixel.woff2', 'pixel-bold.woff2', 'catalog/manifest.json']


def freeze(version, source, notes, root=ROOT):
    if not re.fullmatch(r'\d+\.\d+(?:\.\d+)?', version):
        raise ValueError('Use a numeric version such as 2.9 or 2.9.1')
    destination = root / 'builds' / version
    download = root / 'downloads' / f'SHUFFLER-{version}.zip'
    if destination.exists() or download.exists():
        raise FileExistsError(f'Build {version} is frozen. Choose a new version.')
    manifest = json.loads((source / 'catalog/manifest.json').read_text())
    files = list(FILES)
    total = 0
    for region, info in manifest['regions'].items():
        name = info['file']
        if not re.fullmatch(r'catalog/regions/[a-z-]+\.json', name):
            raise ValueError('Unexpected catalog path')
        rows = json.loads((source / name).read_text())
        if len(rows) != info['count']:
            raise ValueError(f'Catalog count mismatch: {region}')
        total += len(rows)
        files.append(name)
    if total != manifest['total']:
        raise ValueError('Catalog total mismatch')
    for name in files:
        if not (source / name).is_file():
            raise FileNotFoundError(name)
    destination.parent.mkdir(exist_ok=True)
    download.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as temporary:
        staged = Path(temporary)
        for name in files:
            target = staged / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, target)
        page = staged / 'index.html'
        page.write_text(page.read_text().replace('href="./releases.html"', 'href="../../releases.html"'))
        checksums = {name: hashlib.sha256((staged / name).read_bytes()).hexdigest() for name in files}
        (staged / 'build.json').write_text(json.dumps({'version': version, 'notes': notes,
            'catalog_count': total, 'sha256': checksums}, indent=2))
        with ZipFile(download, 'w', ZIP_DEFLATED) as archive:
            for name in files + ['build.json']:
                archive.write(staged / name, name)
        shutil.copytree(staged, destination)
    entries_file = root / 'releases.json'
    entries = json.loads(entries_file.read_text()) if entries_file.exists() else []
    entries.append({'version': version, 'notes': notes, 'count': total})
    entries.sort(key=lambda entry: tuple(map(int, entry['version'].split('.'))), reverse=True)
    entries_file.write_text(json.dumps(entries, indent=2))
    cards = '\n'.join(f'<article><h2>Build {html.escape(e["version"])}</h2><p>{html.escape(e["notes"])}</p>'
        f'<p class="muted">{e["count"]:,} catalog entries · frozen app and catalog</p>'
        f'<a href="./builds/{e["version"]}/">Open build {e["version"]}</a> · '
        f'<a href="./downloads/SHUFFLER-{e["version"]}.zip" download>Download ZIP</a></article>' for e in entries)
    (root / 'releases.html').write_text('<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>Shuffler releases</title>'
        '<link rel="stylesheet" href="./releases.css"></head><body><main>'
        '<a href="./">← Current Shuffler</a><h1>Shuffler releases</h1>'
        '<p>Open any saved build. Each keeps its own app and catalog, so newer releases do not replace it.</p>'
        '<p class="muted">Archived images still depend on the Internet Archive being reachable. '
        'For downloaded builds, serve the extracted folder with a local web server.</p>' + cards + '</main></body></html>')
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('version')
    parser.add_argument('--source', type=Path, default=ROOT)
    parser.add_argument('--notes', required=True)
    args = parser.parse_args()
    print(freeze(args.version, args.source.resolve(), args.notes))
