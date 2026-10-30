from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest

from download_google_era5 import selected_times, request_folder
from merge_downloads import expected_times


class TimeSelectionTests(unittest.TestCase):
    def test_beijing_night_crosses_utc_day(self):
        cfg = dict(time_zone='beijing', start='2018-01-02T20:00:00',
                   end='2018-01-03T08:00:00', hours=[20,21,22,23,0,1,2,3,4,5,6,7,8])
        times = selected_times(cfg)
        self.assertEqual(len(times), 13)
        self.assertEqual(times[0], datetime(2018,1,2,12))
        self.assertEqual(times[-1], datetime(2018,1,3,0))
        self.assertEqual(times, expected_times(cfg))

    def test_legacy_shanghai_keeps_utc_hours(self):
        cfg = dict(time_zone='Asia/Shanghai', start='2018-01-02T08:00:00', end='2018-01-02T23:00:00',hours=[1,2])
        self.assertEqual(selected_times(cfg), [datetime(2018,1,2,1),datetime(2018,1,2,2)])

    def test_explicit_hours_zone(self):
        cfg = dict(time_zone='UTC', hours_time_zone='beijing', start='2018-01-02T00:00:00',end='2018-01-02T23:00:00',hours=[20])
        self.assertEqual(selected_times(cfg), [datetime(2018,1,2,12)])

    def test_equivalent_beijing_reuses_utc_downloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = dict(site='test', output_dir=tmp, time_zone='UTC', start='2018-01-02T12:00:00',end='2018-01-03T00:00:00',hours=list(range(24)))
            folder = Path(tmp) / 'test_legacy'
            folder.mkdir()
            (folder / 'request.json').write_text(json.dumps(dict(config=old,signature='legacy')),encoding='utf-8')
            changed = dict(old,time_zone='beijing',start='2018-01-02T20:00:00',end='2018-01-03T08:00:00')
            self.assertEqual(request_folder(changed),(folder,'legacy'))

    def test_bad_zone_and_non_integer_hours_rejected(self):
        cfg = dict(time_zone='beijing',start='2018-01-02T00:00:00',end='2018-01-02T23:00:00',hours=[True])
        with self.assertRaises(ValueError): selected_times(cfg)
        cfg.update(time_zone='unknown',hours=[1])
        with self.assertRaises(ValueError): selected_times(cfg)


if __name__ == '__main__':
    unittest.main()
