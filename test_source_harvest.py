import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import build_catalog as bc
import harvest_personal_and_rave as harvest


def item(n, source='Rave.ca', region='north-america'):
    return dict(year=2004, source=source, region=region, digest=f'd:{n}',
                visualKey='', original=f'https://rave.ca/{n}.jpg', archive=f'https://web.archive.org/web/20040101000000id_/http://rave.ca/{n}.jpg')


class HarvestTests(unittest.TestCase):
    def test_cdx_mime_dates_host_and_asset_filter(self):
        def row(url, mime='image/jpeg', ts='20040101000000', status='200', digest='abc'):
            return [ts, url, mime, status, digest, '100']
        rows = [['timestamp', 'original', 'mimetype', 'statuscode', 'digest', 'length'],
            row('http://rave.ca/image.php?id=1'),
            row('http://www.rave.ca/flyer.gif', 'image/gif'),
            row('http://rave.ca/art.png', 'image/png'),
            row('http://rave.ca/new.jpg', ts='20090101000000'),
            row('http://rave.ca/old.jpg', ts='20030101000000'),
            row('http://rave.ca/error.jpg', status='404'),
            row('http://rave.ca.evil.test/image.jpg'),
            row('http://rave.ca/a.jpg', digest='-'),
            row('http://rave.ca/logo.gif', 'image/gif'),
            row('http://rave.ca/fake.jpg', 'text/html')]
        with patch.object(bc, 'fetch_json', return_value=rows):
            result = bc.query_wayback_range('Rave.ca', 'rave.ca', 2004, 2004,
                'north-america', 100, 2, image_types=('jpeg', 'png', 'gif'))
        self.assertEqual(len(result), 3)
        self.assertTrue(all(i['year'] == 2004 for i in result))

    def test_merge_full_region_deduplication_and_repeat(self):
        shards = {r: [] for r in bc.REGIONS}
        shards['north-america'] = [item(n, f'Old-{n % 3}') for n in range(6000)]
        shards['africa'] = [item('africa', 'GhanaWeb', 'africa')]
        candidates = [item(n) for n in range(6000, 6800)]
        updated, added = harvest.merge(shards, candidates, 500)
        self.assertEqual(added, {'Rave.ca': 500})
        self.assertEqual(len(updated['north-america']), 6000)
        self.assertEqual(updated['africa'], shards['africa'])
        self.assertEqual(len({i['digest'] for i in updated['north-america']}), 6000)
        # Re-running the same bounded harvest must be a no-op, even at capacity.
        again, counts = harvest.merge(updated, candidates)
        self.assertEqual(again, updated)
        self.assertFalse(counts)

    def test_failure_not_cached_and_empty_success_resumed(self):
        args = SimpleNamespace(source=['Rave.ca'], from_year=2004, to_year=2004,
            limit=10, timeout=1, delay=0, merge=True, per_source=5, transport='urllib')
        with TemporaryDirectory() as directory, patch.object(harvest, 'CACHE', Path(directory)):
            with patch.object(bc, 'query_wayback_range', side_effect=TimeoutError('offline')):
                harvest.run(args)
            self.assertFalse(list(Path(directory).glob('rave.ca*.json')))
            with patch.object(bc, 'query_wayback_range', return_value=[]) as query:
                harvest.run(args)
                harvest.run(args)
                self.assertEqual(query.call_count, 1)
            self.assertEqual(json.loads((Path(directory) / 'summary.json').read_text())['added'], {})


if __name__ == '__main__':
    unittest.main()
