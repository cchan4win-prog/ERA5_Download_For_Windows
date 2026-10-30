"""Verify pause/resume, cooperative stopping, and transfer accounting."""
import tempfile
from pathlib import Path
import threading
import time
import unittest

from download_google_era5 import ROOT, DownloadControl, DownloadStopped, atomic_json, HTTP


class ControlTests(unittest.TestCase):
    def test_pause_resume_and_stop(self):
        with tempfile.TemporaryDirectory(dir=ROOT, prefix='control_test_') as tmp:
            path = Path(tmp) / 'control.json'
            atomic_json(path, {'command': 'pause'})
            control = DownloadControl(path)
            passed = threading.Event()
            thread = threading.Thread(target=lambda: (control.wait(), passed.set()))
            thread.start()
            time.sleep(.25)
            self.assertFalse(passed.is_set())
            atomic_json(path, {'command': 'run'})
            thread.join(timeout=2)
            self.assertTrue(passed.is_set())
            atomic_json(path, {'command': 'stop'})
            time.sleep(.12)
            with self.assertRaises(DownloadStopped):
                control.wait()

    def test_transfer_accounting_is_per_hour(self):
        http = HTTP({})
        http.local.hour = '20160508_00'
        http._received(64)
        http.local.hour = '20160508_01'
        http._received(128)
        self.assertEqual(http.bytes, 192)
        self.assertEqual(http.hour_bytes, {'20160508_00': 64, '20160508_01': 128})


if __name__ == '__main__':
    unittest.main()
