from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest

import download_google_era5 as engine
import download_queue as queue


def sample(root):
    return dict(time_zone='beijing',hours=[20,21],parallel_hours=5,workers=24,output_dir=str(root),generate_real_height=False,
        tasks=[dict(site='A',latitude=10,longitude=20,periods=[dict(start='2019-09-23',end='2019-09-24'),dict(start='2019-10-01',end='2019-10-01')]),
               dict(site='B',latitude=30,longitude=40,time_zone='UTC',parallel_hours=2,periods=[dict(start='2020-01-01',end='2020-01-02')])])


class QueueTests(unittest.TestCase):
    def test_expansion_and_inheritance(self):
        jobs=queue.expand_jobs(sample('data'))
        self.assertEqual([j['site'] for j in jobs],['A','A','B'])
        self.assertEqual([j['parallel_hours'] for j in jobs],[5,5,2])
        self.assertEqual(jobs[0]['end'],'2019-09-24T23:00:00')
        self.assertEqual(engine.selected_times(jobs[0])[0],datetime(2019,9,23,12))
        self.assertEqual(engine.selected_times(jobs[2])[0],datetime(2020,1,1,20))

    def test_period_names_and_collision_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            job=queue.expand_jobs(sample(tmp))[0]
            folder,signature=engine.request_folder(job)
            self.assertEqual(folder.name,'A_2019-09-23_2019-09-24')
            folder.mkdir()
            engine.atomic_json(folder/'request.json',dict(config=job,signature=signature))
            self.assertEqual(engine.request_folder(job),(folder,signature))
            changed=dict(job,longitude=25)
            self.assertNotEqual(engine.request_folder(changed)[0],folder)
            same=dict(job,north=None,south=None,west=None,east=None,generate_real_height=True)
            self.assertEqual(engine.request_folder(same),(folder,signature))

    def test_queue_runs_sequentially_and_stops_all_following_jobs(self):
        for cancel_after in (False, True):
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                config=sample(root/'data')
                control=root/'control.json'
                engine.atomic_json(control,dict(command='run'))
                calls=[]
                def fake_runner(args):
                    cfgpath=Path(args[args.index('--config')+1])
                    status=Path(args[args.index('--status-file')+1])
                    cfg=json.loads(cfgpath.read_text(encoding='utf-8'))
                    calls.append(cfg['site'])
                    folder,signature=engine.request_folder(cfg)
                    folder.mkdir(parents=True,exist_ok=True)
                    n=len(engine.selected_times(cfg))
                    engine.atomic_json(status,dict(complete=True,requested_hours=n,completed_hours=n,data_folder=str(folder),network_bytes_this_run=10,seconds_per_hour=2.,eta_seconds=0))
                    engine.atomic_json(folder/'merge_status.json',dict(state='complete',output=str(folder/'merged.nc')))
                    if cancel_after: engine.atomic_json(control,dict(command='stop'))
                original=queue.ROOT
                queue.ROOT=root
                try:
                    result=queue.run_queue(config,root/'session',root/'status.json',control,fake_runner)
                finally:
                    queue.ROOT=original
                self.assertEqual(calls,['A'] if cancel_after else ['A','A','B'])
                self.assertEqual(result['run_state'],'cancelled' if cancel_after else 'complete')
                self.assertGreaterEqual(result['elapsed_active_seconds'],0)
                if not cancel_after:
                    self.assertEqual(result['queue_completed_tasks'],3)
                    self.assertEqual(result['eta_seconds'],0)

    def test_invalid_later_task_rejected_before_any_download(self):
        cfg=sample('data')
        cfg['tasks'][1]['latitude']=None
        with self.assertRaisesRegex(ValueError,'latitude/longitude'):
            queue.expand_jobs(cfg)
        cfg['tasks'][1]['enabled']=False
        self.assertEqual(len(queue.expand_jobs(cfg)),2)

    def test_elapsed_time_is_persisted_between_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            cfg=sample(root/'data')
            cfg['tasks']=cfg['tasks'][:1]
            cfg['tasks'][0]['periods']=cfg['tasks'][0]['periods'][:1]
            control=root/'control.json'
            engine.atomic_json(control,dict(command='run'))
            def fake_runner(args):
                job=json.loads(Path(args[args.index('--config')+1]).read_text(encoding='utf-8'))
                folder,_=engine.request_folder(job)
                folder.mkdir(parents=True,exist_ok=True)
                n=len(engine.selected_times(job))
                engine.atomic_json(Path(args[args.index('--status-file')+1]),dict(complete=True,requested_hours=n,completed_hours=n,data_folder=str(folder)))
                engine.atomic_json(folder/'merge_status.json',dict(state='complete',output=str(folder/'merged.nc')))
            previous=queue.ROOT
            queue.ROOT=root
            try:
                first=queue.run_queue(cfg,root/'session1',root/'status1.json',control,fake_runner)
                second=queue.run_queue(cfg,root/'session2',root/'status2.json',control,fake_runner)
            finally:
                queue.ROOT=previous
            self.assertGreater(second['elapsed_active_seconds'],first['elapsed_active_seconds'])


if __name__ == '__main__':
    unittest.main()
