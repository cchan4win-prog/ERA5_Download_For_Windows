"""Verify chronological merging and rejection of missing hourly inputs."""
import json
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from download_google_era5 import ROOT, np, write_hour, open_nc
from merge_downloads import merge_downloads


class MergeTests(unittest.TestCase):
    def make_request(self, folder, selected):
        cfg = dict(site='test', time_zone='UTC', start='2016-05-08T00:00:00',
                   end='2016-05-08T02:00:00', hours=list(range(24)))
        (folder / 'request.json').write_text(json.dumps(dict(config=cfg, signature='sample')), encoding='utf-8')
        for hour in selected:
            stamp = datetime(2016, 5, 8) + timedelta(hours=hour)
            arrays = {v: np.full((1, 137, 1, 1), hour + index, dtype='f4')
                      for index, v in enumerate(('u', 'v', 't', 'q'))}
            arrays.update({v: np.full((1, 1, 1), hour, dtype='f4') for v in ('sp', 'z', 'lnsp')})
            write_hour(folder / f'{stamp:%Y%m%d_%H}.nc', stamp, arrays,
                       {v: dict(units='1') for v in arrays}, np.array([41.]), np.array([100.25]),
                       np.arange(1, 138), cfg, 'sample')

    def test_all_values_and_hourly_originals_retained(self):
        with tempfile.TemporaryDirectory(dir=ROOT, prefix='merge_test_') as tmp:
            folder = Path(tmp)
            self.make_request(folder, [2, 0, 1])
            output = merge_downloads(folder)
            with open_nc(output) as ds:
                self.assertEqual(len(ds.dimensions['time']), 3)
                np.testing.assert_array_equal(ds['u'][:, 0, 0, 0], [0, 1, 2])
                np.testing.assert_array_equal(ds['sp'][:, 0, 0], [0, 1, 2])
                np.testing.assert_array_equal(np.diff(ds['time'][:]), [1, 1])
            self.assertEqual(len(list(folder.glob('*.nc'))), 3)
            report = json.loads((folder / 'merge_status.json').read_text(encoding='utf-8'))
            self.assertEqual(report['state'], 'complete')
            self.assertEqual(report['verified_hours'], 3)

    def test_missing_hour_rejected(self):
        with tempfile.TemporaryDirectory(dir=ROOT, prefix='merge_test_') as tmp:
            folder = Path(tmp)
            self.make_request(folder, [0, 2])
            with self.assertRaisesRegex(ValueError, '1 hours missing'):
                merge_downloads(folder)
            self.assertFalse((folder / 'merged').exists())


if __name__ == '__main__':
    unittest.main()
