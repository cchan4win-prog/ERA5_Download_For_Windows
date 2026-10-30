"""Check byte-range block reconstruction against complete Blosc decompression."""
import unittest
import struct
import tempfile
from pathlib import Path
import numpy as np
from download_google_era5 import Blosc, decode_blocks, selection, HTTP, request_folder, read_cached_task
import json
from concurrent.futures import ThreadPoolExecutor
import requests


class RangeReaderTests(unittest.TestCase):
    def test_parallel_cache_isolation(self):
        http = HTTP(dict(retries=0, timeout_seconds=1))
        barrier = __import__('threading').Barrier(3)
        class FakeStore:
            def read(self, var, key, indices):
                barrier.wait(timeout=5)
                return str(http.local.cache_dir)
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs = [pool.submit(read_cached_task, http, Path(str(h)), FakeStore(), '', (), []) for h in range(3)]
            self.assertEqual([j.result() for j in jobs], [str(Path(str(h)) / 'ranges') for h in range(3)])

    def test_concurrency_change_reuses_legacy_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = dict(site='test', output_dir=tmp, workers=12, latitude=41.)
            folder = Path(tmp) / 'test_legacy'
            folder.mkdir()
            (folder / 'request.json').write_text(json.dumps(dict(config=cfg, signature='legacy')), encoding='utf-8')
            changed = dict(cfg, workers=24, parallel_hours=3)
            self.assertEqual(request_folder(changed), (folder, 'legacy'))

    def test_resume_after_process_restart_and_cache_integrity(self):
        payload = bytes(range(64))
        seen = []

        class Response:
            status_code = 206
            def __init__(self, offset, interrupt):
                self.offset, self.interrupt = offset, interrupt
                self.headers = {'Content-Range': f'bytes {offset}-63/64', 'ETag': '"version-1"'}
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def raise_for_status(self): pass
            def iter_content(self, chunk_size):
                if self.interrupt:
                    yield payload[:7]
                    raise requests.ConnectionError('simulated shutdown/network loss')
                yield payload[self.offset:]

        class Session:
            def __init__(self, interrupt=False): self.interrupt = interrupt
            def get(self, url, headers, **kwargs):
                seen.append(dict(headers))
                offset = int(headers['Range'].split('=')[1].split('-')[0])
                return Response(offset, self.interrupt)

        cfg = dict(retries=0, timeout_seconds=1)
        with tempfile.TemporaryDirectory() as tmp:
            first = HTTP(cfg)
            first.cache_dir = Path(tmp)
            first.local.session = Session(interrupt=True)
            with self.assertRaises(requests.ConnectionError):
                first.get('https://example.invalid/object', 0, 63)
            self.assertEqual(next(Path(tmp).glob('*.part')).stat().st_size, 7)
            restarted = HTTP(cfg)
            restarted.cache_dir = Path(tmp)
            restarted.local.session = Session()
            self.assertEqual(restarted.get('https://example.invalid/object', 0, 63), payload)
            self.assertEqual(seen[-1]['Range'], 'bytes=7-63')
            self.assertEqual(seen[-1]['If-Match'], '"version-1"')
            self.assertEqual(restarted.bytes, 57)
            self.assertEqual(restarted.get('https://example.invalid/object', 0, 63), payload)
            self.assertEqual(len(seen), 2)
            self.assertEqual(restarted.cache_hits, 1)
            next(Path(tmp).glob('*.bin')).write_bytes(b'x' * 64)
            self.assertEqual(restarted.get('https://example.invalid/object', 0, 63), payload)
            self.assertEqual(len(seen), 3)
            self.assertEqual(seen[-1]['Range'], 'bytes=0-63')

    def test_blosc_blocks(self):
        rng = np.random.default_rng(81)
        for length in [150003, 262144]:
            for shuffle in [0, 1]:
                for clevel in [0, 5]:
                    for compressor in ['lz4', 'zstd']:
                        values = rng.normal(size=length).astype('f4')
                        encoded = bytes(Blosc(cname=compressor, clevel=clevel,
                                        shuffle=shuffle, blocksize=65536).encode(values))
                        header = encoded[:16]
                        _, _, flags, _, nbytes, blocksize, _ = struct.unpack('<BBBBIII', header)
                        count = (nbytes + blocksize - 1) // blocksize
                        indices = np.unique(np.r_[0, length-1, np.arange(0, length, 16384), rng.integers(0, length, 200)]).astype('int64')
                        ranges = []
                        def fetch(a, b):
                            ranges.append((a, b))
                            return encoded[a:b+1]
                        got = decode_blocks(header, encoded[16:16+4*count], fetch, indices, '<f4')
                        np.testing.assert_array_equal(values[indices], got)

    def test_geographic_selection(self):
        lat = np.arange(90, -90.01, -.25)
        lon = np.arange(0, 360, .25)
        iy, ix = selection(dict(spatial_mode='point', latitude=40.96, longitude=100.29), lat, lon)
        self.assertEqual(float(lat[iy[0]]), 41.)
        self.assertEqual(float(lon[ix[0]]), 100.25)
        iy, ix = selection(dict(spatial_mode='bbox', south=40.75, north=41.25, west=-.25, east=.25), lat, lon)
        np.testing.assert_array_equal(lon[ix], [0, .25, 359.75])
        self.assertEqual(len(iy), 3)


if __name__ == '__main__':
    unittest.main()
