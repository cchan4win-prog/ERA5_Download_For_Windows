from datetime import datetime, timedelta
from pathlib import Path
import json
import tempfile
import unittest

from download_google_era5 import ROOT, np, write_hour, open_nc, atomic_json
from compute_real_heights import hydrostatic_heights, convert_real_heights, A, B, RD, G0, EARTH_RADIUS
from merge_downloads import merge_downloads
import download_queue as queue


def make_hour(folder, cfg, signature, stamp):
    arrays=dict(t=np.full((1,137,1,1),280.,dtype='f4'),q=np.full((1,137,1,1),.003,dtype='f4'),
                u=np.full((1,137,1,1),12.,dtype='f4'),v=np.full((1,137,1,1),-3.,dtype='f4'),
                sp=np.full((1,1,1),90000.,dtype='f4'),lnsp=np.full((1,1,1),np.log(90000.),dtype='f4'),
                z=np.full((1,1,1),1000.*G0,dtype='f4'))
    units=dict(t='K',q='kg kg-1',u='m s-1',v='m s-1',sp='Pa',lnsp='1',z='m2 s-2')
    write_hour(folder/f'{stamp:%Y%m%d_%H}.nc',stamp,arrays,{n:dict(units=units[n]) for n in arrays},np.array([40.]),np.array([100.]),np.arange(1,138),cfg,signature)


class RealHeightTests(unittest.TestCase):
    def test_isothermal_top_has_independent_analytic_solution(self):
        t=np.full((2,137,2,2),280.)
        q=np.zeros_like(t)
        lnsp=np.full((2,2,2),np.log(100000.))
        surface=np.full((2,2,2),1000.*G0)
        values=hydrostatic_heights(t,q,lnsp,surface)
        top_phi=surface+RD*280.*np.log(100000./(A[1]/2))
        expected=EARTH_RADIUS*(top_phi/G0)/(EARTH_RADIUS-top_phi/G0)
        np.testing.assert_allclose(values['h_full'][:,0],expected,rtol=1e-12)
        self.assertTrue((np.diff(values['p_full'],axis=1)>0).all())
        self.assertTrue((np.diff(values['h_full'],axis=1)<0).all())
        self.assertTrue((values['h_full'][:,-1]>1000).all())

    def test_q_is_not_mutated_or_clipped(self):
        t=np.full((1,137,1,1),280.)
        q=np.full_like(t,-1e-6)
        before=q.copy()
        hydrostatic_heights(t,q,np.full((1,1,1),np.log(90000)),np.zeros((1,1,1)))
        np.testing.assert_array_equal(q,before)

    def test_convert_preserves_weather_and_validates_all_hours(self):
        with tempfile.TemporaryDirectory(dir=ROOT,prefix='height_test_') as tmp:
            folder=Path(tmp)
            cfg=dict(site='height_test',time_zone='UTC',start='2020-01-01T00:00:00',end='2020-01-01T01:00:00',hours=list(range(24)))
            atomic_json(folder/'request.json',dict(config=cfg,signature='height_unit'))
            for hour in range(2):make_hour(folder,cfg,'height_unit',datetime(2020,1,1)+timedelta(hours=hour))
            merged=merge_downloads(folder)
            result=convert_real_heights(merged,chunk_time=1)
            self.assertEqual(result.parent,merged.parent)
            with open_nc(result) as ds,open_nc(merged) as source:
                np.testing.assert_array_equal(ds['u'][:],source['u'][:])
                np.testing.assert_array_equal(ds['q'][:],source['q'][:])
                self.assertEqual(ds['h_full'].shape,(2,137,1,1))
                self.assertEqual(ds['u'].coordinates,'h_full')
                self.assertEqual(ds['p_full'].units,'Pa')
                self.assertTrue((np.diff(ds['h_full'][:],axis=1)<0).all())
            modified=result.stat().st_mtime_ns
            self.assertEqual(convert_real_heights(merged),result)
            self.assertEqual(result.stat().st_mtime_ns,modified)
            report=json.loads(result.with_suffix('.status.json').read_text(encoding='utf-8'))
            self.assertEqual(report['verified_hours'],2)

    def test_queue_creates_real_height_after_merge(self):
        with tempfile.TemporaryDirectory(dir=ROOT,prefix='height_queue_') as tmp:
            root=Path(tmp)
            cfg=dict(time_zone='UTC',hours=[0],output_dir=str(root/'data'),generate_real_height=True,
                tasks=[dict(site='test',latitude=40.,longitude=100.,periods=[dict(start='2020-01-01T00:00:00',end='2020-01-01T00:00:00')])])
            control=root/'control.json';atomic_json(control,dict(command='run'))
            def runner(args):
                job=json.loads(Path(args[args.index('--config')+1]).read_text(encoding='utf-8'))
                folder,signature=queue.engine.request_folder(job);folder.mkdir(parents=True)
                atomic_json(folder/'request.json',dict(config=job,signature=signature))
                make_hour(folder,job,signature,datetime(2020,1,1))
                merge_downloads(folder)
                atomic_json(Path(args[args.index('--status-file')+1]),dict(complete=True,requested_hours=1,completed_hours=1,data_folder=str(folder)))
            previous=queue.ROOT;queue.ROOT=root
            try:status=queue.run_queue(cfg,root/'session',root/'status.json',control,runner)
            finally:queue.ROOT=previous
            self.assertEqual(status['run_state'],'complete')
            self.assertTrue(Path(status['queue_tasks'][0]['height_file']).exists())

    def test_missing_model_levels_rejected(self):
        with self.assertRaises(ValueError):
            hydrostatic_heights(np.ones((1,1,1,1))*280,np.zeros((1,1,1,1)),np.zeros((1,1,1)),np.zeros((1,1,1)))


if __name__=='__main__':unittest.main()
