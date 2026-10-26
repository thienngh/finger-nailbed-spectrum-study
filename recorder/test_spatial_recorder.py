"""Spatial archive/annotation/reflection tests; no camera required."""
import csv, importlib.util,json,queue,signal,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import URLError,HTTPError
import numpy as np
ROOT=Path(__file__).resolve().parent;sys.path.insert(0,str(ROOT))
TEST_WORK=ROOT/'test-output';TEST_WORK.mkdir(exist_ok=True)
import spatial_recording as sp
import export_spatial as ex
import fast_spectrum as fast
import study_session as study

class SpatialTests(unittest.TestCase):
    def test_study_labels_and_persisted_arm_timer(self):
        import time
        labels=dict(participant_id='p01',arm='left',site='nailbed',round=1,roi_confirmed=True)
        self.assertEqual(study.validate_labels(labels),('P01','left','nailbed',1))
        for bad in (dict(labels,participant_id='../escape'),dict(labels,arm=''),dict(labels,site=''),dict(labels,roi_confirmed=False)):
            with self.assertRaises(ValueError):study.validate_labels(bad)
        with tempfile.TemporaryDirectory(dir=str(TEST_WORK)) as tmp:
            st=study.Study(tmp,'S01',900)
            folder=st.new_folder('P01','left','nailbed','clear_clamp')
            st.intervention('P01','left','released',folder)
            st=study.Study(tmp,'S01',900)
            with self.assertRaises(ValueError):st.check_rest('P01','left','clear_clamp')
            st.check_rest('P01','right','clear_clamp')
            st.check_rest('P02','left','clear_clamp')
            st.check_rest('P01','left','resting')
            timer=st.root/'P01/left/last_intervention.json'
            saved=json.loads(timer.read_text());saved['epoch_s']=time.time()-901;timer.write_text(json.dumps(saved))
            st.check_rest('P01','left','clear_clamp')

    def test_center_and_margin(self):
        coords=np.array([[40,40],[50,50],[60,60],[80,80]])
        spectra=np.ones((4,96));spectra[2,3]=np.nan
        roi,bad,valid=sp.spatial_masks(spectra,coords,(100,100),[.2,.2,.8,.8],[[.45,.45,.55,.55]],6)
        self.assertEqual(bad.tolist(),[True,True,True,False])
        self.assertFalse(valid.any())
        np.testing.assert_equal(sp.centered_xy(coords,(100,100),[.2,.2,.8,.8]),[[-10,-10],[0,0],[10,10],[30,30]])
    def test_point_reference_and_weak_white(self):
        cap=sp.PointReference('dark',0,np.array([[1,2],[3,4]]),np.array([500,600]),{},[0,0,1,1])
        vals=[]
        for i in range(10):
            sample=np.array([[i,i+1],[i+2,i+3]],dtype=float);vals.append(sample);cap.add(sample,np.ones((5,5)),i)
        result=cap.content();np.testing.assert_allclose(result['spectra_mean'],np.mean(vals,axis=0));np.testing.assert_allclose(result['spectra_temporal_sd'],np.std(vals,axis=0,ddof=1))
        r,valid=sp.relative_reflectance(np.array([[4.,8.]]),np.array([[2.,3.]]),np.array([[6.,3.]]))
        self.assertEqual(r[0,0],.5);self.assertTrue(np.isnan(r[0,1]));self.assertFalse(valid[0,1])
    def test_memory_limit(self):
        failures=[];rec=sp.Recorder(failures.append,max_mib=.0001,min_free_gb=0)
        with self.assertRaises(RuntimeError):rec.put(('frame',(1,{'spectra':np.ones((100,96))})))
        rec.close();self.assertTrue(failures);self.assertEqual(rec.pending_bytes,0)
    def test_acquisition_overflow_is_visible(self):
        class Fake:
            lock=__import__('threading').RLock();acquired=0;acquired_times=[];dropped=0;stage='pressure'
            def fail(self,msg):self.error=msg
        app=Fake();q=queue.Queue(1);q.put((0,0,'',None));fast.enqueue(app,q,(1,1,'',None))
        self.assertEqual(app.dropped,1);self.assertIn('incomplete',app.error)
    def test_incomplete_archive_flag(self):
        with tempfile.TemporaryDirectory(dir=str(TEST_WORK)) as tmp:
            errors=[];rec=sp.Recorder(errors.append,min_free_gb=0);folder=Path(tmp)/'trial'
            rec.put(('start',(folder,np.array([500.,600.]),{})))
            rec.put(('finish',dict(rows=1,reason='interrupted_incomplete',error=None)))
            rec.close()
            self.assertFalse(errors)
            self.assertFalse(json.loads((folder/'recording_status.json').read_text())['complete'])
            self.assertFalse(json.loads((folder/'summary.json').read_text())['archive_complete'])
    def test_camera_sized_frame_roundtrip(self):
        with tempfile.TemporaryDirectory(dir=str(TEST_WORK)) as t:
            p=Path(t)/'full.npz';s=np.arange(4384*96,dtype=np.float32).reshape(4384,96);scene=np.arange(2048*2432,dtype=np.uint16).reshape(2048,2432)
            sp.npz_write(p,{'spectra':s,'scene_image':scene})
            with np.load(p,allow_pickle=False) as z:
                np.testing.assert_array_equal(s,z['spectra']);np.testing.assert_array_equal(scene,z['scene_image'])


def integration():
 with tempfile.TemporaryDirectory(dir=str(TEST_WORK)) as tmp:
  tmp=Path(tmp);log=tmp/'server.log'
  with log.open('w') as logfile:
   p=subprocess.Popen([sys.executable,str(ROOT/'fast_spectrum.py'),'--simulate','--retest-seconds','0','--study-id','SIM_STUDY','--fps','30','--port','18766','--baseline-seconds','.8','--white-seconds','.6','--recovery-seconds','.7','--min-free-gb','0','--require-references','--output',str(tmp/'recordings')],stdout=logfile,stderr=logfile)
   def get():return json.load(urlopen('http://127.0.0.1:18766/data',timeout=3))
   def post(name,data={}):
    if name=='start':data=dict(dict(participant_id='SIM01',arm='left',site='nailbed',round=1,roi_confirmed=True),**data)
    return json.load(urlopen(Request('http://127.0.0.1:18766/'+name,data=json.dumps(data).encode(),headers={'Content-Type':'application/json'},method='POST'),timeout=3))
   def wait(pred,seconds=12):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
     try:
      d=get()
      if pred(d):return d
     except URLError:pass
     time.sleep(.03)
    raise AssertionError(log.read_text())
   try:
    wait(lambda d:d['decoded']>3);post('roi',[.1,.1,.9,.9]);wait(lambda d:d['count']>10)
    try:post('start',{'type':'clear_clamp'});raise AssertionError('Missing references should reject start')
    except HTTPError as e:assert e.code==400
    post('dark');wait(lambda d:d['dark_ready'] and not d['reference_collecting'])
    post('white');wait(lambda d:d['point_white_ready'] and not d['reference_collecting'])
    post('start',{'type':'clear_clamp','participant_id':'SIM01','site':'nailbed','distance_cm':25,'lighting':'synthetic'})
    wait(lambda d:d['stage']=='ready');post('pressure');post('exclusions',{'rectangles':[[.1,.1,.3,.3]]});time.sleep(.15)
    post('artifact',{'active':True});time.sleep(.12);post('artifact',{'active':False});time.sleep(.1);post('release')
    d=wait(lambda d:d['stage']=='done' and d['archive_complete']);folder=Path(d['folder'])
    end=time.monotonic()+10
    while not (folder/'summary.json').exists() and time.monotonic()<end:time.sleep(.03)
    summary=json.loads((folder/'summary.json').read_text());assert summary['archive_complete'],summary
    manifest=list(csv.DictReader((folder/'frames.csv').open()));roirows=list(csv.DictReader((folder/'roi_spectra.csv').open()))
    assert len(manifest)==len(roirows)==summary['spatial_frames_written']==summary['rows']
    assert summary['achieved_roi_hz']>25,summary
    assert any(r['artifact_marked']=='True' and r['usable_points']=='0' for r in manifest)
    first=folder/manifest[0]['path']
    with np.load(first,allow_pickle=False) as z:
     assert z['spectra'].shape==(d['all_point_count'],96)
     assert z['scene_image'].shape==(240,320)
     np.testing.assert_allclose(z['coordinates_xy_centered'],z['coordinates_yx'][:,::-1]-[160,120])
     assert str(z['host_received_utc']);seq=int(z['frame'])
    ann={'ranges':[{'start_frame':seq,'end_frame':seq,'artifact':True}]}
    dest=ex.export(folder,tmp/'export',ann,True,True,True,common_points=True)
    out=list(csv.DictReader((dest/'frame_spectra.csv').open()));assert len(out)==len(manifest) and out[0]['usable_frame']=='False'
    good=[r for r in out if r['usable_frame']=='True'];assert good
    columns=[k for k in out[0] if k.endswith('_relative_reflectance')]
    values=np.array([[float(r[k]) for k in columns] for r in good]);assert np.isfinite(values).all() and ((values>.3)&(values<.7)).all()
    import gzip
    with gzip.open(dest/'point_spectra.csv.gz','rt') as f:point_rows=sum(1 for _ in csv.DictReader(f))
    assert point_rows==len(manifest)*d['all_point_count']
    assert json.loads((dest/'export_summary.json').read_text())['common_point_ids']
    ex.plot_export(dest)
    assert (dest/'release_original_rate.png').exists()
    assert (dest/'review.html').exists() and len(list(dest.glob('review_*.jpg')))==len(manifest)
    sp.json_write((TEST_WORK/'validation.json'),dict(passed=True,mode='SIMULATION',spatial_frames=len(manifest),all_points=d['all_point_count'],bands=96,achieved_hz=summary['achieved_roi_hz'],all_point_csv_rows=point_rows,tests='lossless images/spectra, paired references, per-frame masks, artifact retention, full-rate export, ROI-centered xy, missing-ref guard'))
    print('PASS spatial HTTP / exporter:',summary['achieved_roi_hz'],'Hz,',len(manifest),'full frames;',point_rows,'point rows')
    # A second trial must not leak the first trial's events or open files.
    post('start',{'type':'sham','participant_id':'SIM02','arm':'right'});wait(lambda d:d['stage']=='ready');post('pressure');post('release');d2=wait(lambda d:d['stage']=='done' and d['archive_complete'])
    end=time.monotonic()+5
    while not (Path(d2['folder'])/'summary.json').exists() and time.monotonic()<end:time.sleep(.03)
    events=list(csv.DictReader((Path(d2['folder'])/'events.csv').open()));assert any(r['event']=='control_action_started' for r in events)
    post('start',{'type':'clear_clamp','round':2});wait(lambda d:d['stage']=='ready');post('pressure');post('release');d3=wait(lambda d:d['stage']=='done' and d['archive_complete'])
    assert d3['folder']!=str(folder) and '/SIM01/left/nailbed/clear_clamp/' in d3['folder']
    config=json.loads((Path(d3['folder'])/'session.json').read_text());assert config['trial_info']['round']==2 and config['trial_info']['arm']=='left'
    status=wait(lambda d:len(d['study']['trials'])==3 and sum(t['eligible_repeat'] for t in d['study']['trials'])==2);assert status['study']['participants']==['SIM01','SIM02']
    assert sum(t['eligible_repeat'] for t in status['study']['trials'])==2
    print('PASS participant return, left/right labels, per-trial folders and study inventory')
   finally:
    p.send_signal(signal.SIGINT)
    try:p.wait(timeout=10)
    except subprocess.TimeoutExpired:p.kill();p.wait()
    if p.returncode:raise AssertionError(log.read_text())

if __name__=='__main__':
 result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SpatialTests))
 if not result.wasSuccessful():sys.exit(1)
 integration()
