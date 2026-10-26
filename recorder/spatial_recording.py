"""Lossless decoded-frame archives, references and explicit spatial masks.

Python 3.8 compatible. Requires only NumPy/Pillow already used by the viewer.
Decoded NPZ files are NOT encoded SDK .loraw files. No automatic tissue classifier.
"""
import csv
import json
import os
import queue
import shutil
import threading
from pathlib import Path
import numpy as np

CONDITIONS = ('pressure_release', 'black_clamp', 'clear_clamp', 'transparent_plate',
              'sham', 'motion_control', 'resting', 'arterial_occlusion', 'venous_occlusion')
PRESSURE_CONDITIONS = ('pressure_release', 'black_clamp', 'clear_clamp', 'transparent_plate',
                       'arterial_occlusion', 'venous_occlusion')

def safe_json(x):
    if isinstance(x, np.ndarray): return safe_json(x.tolist())
    if isinstance(x, np.generic): return safe_json(x.item())
    if isinstance(x, float) and not np.isfinite(x): return None
    if isinstance(x, dict): return {str(k):safe_json(v) for k,v in x.items()}
    if isinstance(x, (list,tuple)): return [safe_json(v) for v in x]
    if x is None or isinstance(x,(str,int,float,bool)): return x
    return repr(x)

def json_write(path, content):
    temp=path.with_name(path.name+'.tmp')
    temp.write_text(json.dumps(safe_json(content),indent=2,allow_nan=False))
    os.replace(str(temp),str(path))

def npz_write(path, content):
    temp=path.with_name(path.name+'.tmp')
    with temp.open('wb') as f:
        # No lossy conversion, no object arrays, no compression on acquisition thread.
        np.savez(f, **content)
    os.replace(str(temp),str(path))

def rectangle_mask(coords_yx, shape, rectangle, margin_px=0):
    if rectangle is None: return np.zeros(len(coords_yx),dtype=bool)
    h,w=shape[:2];x0,y0,x1,y1=np.asarray(rectangle)*[w,h,w,h]
    y,x=np.asarray(coords_yx).T
    return ((x>=x0-margin_px)&(x<x1+margin_px)&(y>=y0-margin_px)&(y<y1+margin_px))

def spatial_masks(spectra,coords,shape,roi,exclusions=(),margin_px=0):
    roi_mask=rectangle_mask(coords,shape,roi)
    excluded=np.zeros(len(coords),dtype=bool)
    for box in exclusions: excluded |= rectangle_mask(coords,shape,box,margin_px)
    finite=np.isfinite(spectra).all(axis=1)
    return roi_mask,excluded,roi_mask&~excluded&finite

def centered_xy(coords_yx,shape,roi):
    h,w=shape[:2];center=np.array([(roi[0]+roi[2])*w/2,(roi[1]+roi[3])*h/2])
    return np.asarray(coords_yx)[:,::-1]-center

def relative_reflectance(spectra,dark,white,min_signal=1e-6):
    """(sample-dark)/(white-dark); invalid/weak bands stay NaN, never clipped.

    White standard assumed unit reflectance. This is a reference correction,
    NOT certified absolute radiometry or a validated physiological measurement.
    """
    if spectra.shape!=dark.shape or white.shape!=dark.shape:
        raise ValueError('Per-point dark/white/sample shapes disagree')
    denominator=white-dark
    valid=(np.isfinite(spectra)&np.isfinite(dark)&np.isfinite(white)&(denominator>min_signal))
    result=np.full(spectra.shape,np.nan,dtype=np.float64)
    np.divide(spectra-dark,denominator,out=result,where=valid)
    return result,valid

class PointReference:
    """Per-point temporal means/SD with bounded memory (Welford)."""
    def __init__(self,kind,start,coords,waves,settings,roi):
        self.kind=kind;self.start=start;self.coords=np.array(coords,copy=True)
        self.waves=np.array(waves,copy=True);self.settings=dict(settings);self.roi=np.array(roi,copy=True)
        self.count=0;self.mean=None;self.m2=None;self.first=None;self.last=None
        self.scene=None
    def add(self,spectra,scene,t):
        if t<self.start:return
        s=np.asarray(spectra,dtype=np.float64)
        if not np.isfinite(s).all():raise ValueError('Reference has nonfinite samples; recapture')
        if self.mean is None:self.mean=np.zeros_like(s);self.m2=np.zeros_like(s);self.first=t
        if s.shape!=self.mean.shape:raise ValueError('Reference sample geometry changed')
        self.count+=1;delta=s-self.mean;self.mean+=delta/self.count;self.m2+=delta*(s-self.mean)
        self.last=t;self.scene=np.array(scene,copy=True)
    def content(self):
        if self.count<10:raise ValueError('Reference needs at least 10 valid frames')
        return dict(spectra_mean=self.mean,spectra_temporal_sd=np.sqrt(self.m2/(self.count-1)),
                    coordinates_yx=self.coords,wavelengths_nm=self.waves,
                    scene_image=self.scene,frame_count=np.array(self.count),
                    first_host_received_s=np.array(self.first),last_host_received_s=np.array(self.last),
                    roi_normalized=self.roi)

def nbytes(x):
    if isinstance(x,np.ndarray):return x.nbytes
    if isinstance(x,dict):return sum(nbytes(v) for v in x.values())
    if isinstance(x,(tuple,list)):return sum(nbytes(v) for v in x)
    if isinstance(x,bytes):return len(x)
    return 0

class Recorder:
    """Single ordered disk writer with bounded queued memory and visible failures."""
    def __init__(self,fail,max_mib=256,min_free_gb=2):
        self.q=queue.Queue(256);self.fail=fail;self.max_bytes=int(max_mib*1024**2)
        self.min_free=int(min_free_gb*1024**3);self.pending_bytes=0
        self.guard=threading.Lock();self.failed=False;self.folder=None;self.last_summary=None
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
    def put(self,item):
        if self.failed:raise RuntimeError('Disk writer has failed; recording incomplete')
        size=nbytes(item)
        with self.guard:
            full=self.pending_bytes+size>self.max_bytes
            if not full:self.pending_bytes+=size
        if full:
            message='Disk queue memory limit reached; trial incomplete. Use faster storage or reduce target rate.'
            self.fail(message);raise RuntimeError(message)
        try:self.q.put_nowait((item,size))
        except queue.Full:
            with self.guard:self.pending_bytes-=size
            self.fail('Disk writer queue full; trial incomplete');raise RuntimeError('Recording queue full')
    def run(self):
        handles=[];summary=None;frame_count=0;written_bytes=0
        try:
            while True:
                packed=self.q.get()
                if packed is None:break
                item,size=packed
                try:
                    kind,payload=item
                    if kind=='reference':
                        path,reference=payload;path.parent.mkdir(parents=True,exist_ok=True);json_write(path,reference)
                    elif kind=='point_reference':
                        path,content,meta=payload;path.parent.mkdir(parents=True,exist_ok=True)
                        npz_write(path,content);json_write(path.with_suffix('.json'),meta)
                    elif kind=='start':
                        for f in handles:f.close()
                        handles=[];folder,waves,config=payload;folder.mkdir(parents=True)
                        self.folder=folder;frame_count=written_bytes=0;self.last_summary=None
                        json_write(folder/'recording_status.json',{'complete':False,'reason':'recording_or_interrupted'})
                        json_write(folder/'session.json',config)
                        (folder/'frames').mkdir()
                        for label,ref in config.get('point_references',{}).items():
                            if ref:
                                shutil.copyfile(ref['path'],folder/(label+'_points.npz'))
                                shutil.copyfile(str(Path(ref['path']).with_suffix('.json')),folder/(label+'_points.json'))
                        f=(folder/'roi_spectra.csv').open('w',newline='');handles.append(f);writer=csv.writer(f)
                        writer.writerow(['frame','host_received_s','host_received_utc','decoded_s','trial_s','phase','sample_count','peak_nm','camera_metadata_times_json']+['%.5f_nm'%x for x in waves])
                        e=(folder/'events.csv').open('w',newline='');handles.append(e);events=csv.writer(e)
                        events.writerow(['host_s','host_utc','event','details'])
                        ix=(folder/'lo_ratio.csv').open('w',newline='');handles.append(ix);index_writer=csv.writer(ix)
                        index_writer.writerow(['frame','host_received_s','trial_s','phase','ratio_mean','valid_points','selected_points','change_from_index_baseline_percent','frame_norm_min','frame_norm_max'])
                        mf=(folder/'frames.csv').open('w',newline='');handles.append(mf);manifest=csv.writer(mf)
                        manifest.writerow(['frame','host_received_s','host_received_utc','trial_s','phase','path','spectral_points','bands','roi_points','usable_points','artifact_marked','bytes'])
                        if config.get('white_reference'):json_write(folder/'white_reference.json',config['white_reference'])
                    elif kind=='frame':
                        seq,content=payload;path=self.folder/'frames'/('frame_%09d.npz'%seq)
                        if frame_count%30==0 and shutil.disk_usage(self.folder).free<self.min_free:
                            raise OSError('Less than minimum free disk space; trial incomplete')
                        npz_write(path,content);length=path.stat().st_size;written_bytes+=length;frame_count+=1
                        manifest.writerow([seq,float(content['host_received_s']),str(content['host_received_utc']),float(content['trial_s']),str(content['phase']),str(path.relative_to(self.folder)),len(content['spectra']),len(content['wavelengths_nm']),int(content['roi_mask'].sum()),int(content['usable_mask'].sum()),bool(content['artifact_marked']),length])
                    elif kind=='row':writer.writerow(payload)
                    elif kind=='event':
                        events.writerow(payload)
                        for f in handles:f.flush()
                    elif kind=='index':index_writer.writerow(payload)
                    elif kind=='index_baseline':json_write(self.folder/'lo_ratio_baseline.json',payload)
                    elif kind=='baseline':
                        waves,mean,std,count,duration=payload
                        with (self.folder/'baseline.csv').open('w',newline='') as f:
                            bw=csv.writer(f);bw.writerow(['wavelength_nm','mean_signal_au','temporal_sd_au','baseline_frames','duration_s'])
                            bw.writerows(zip(waves,mean,std,[count]*len(waves),[duration]*len(waves)))
                    elif kind=='preview':
                        name,jpeg=payload;(self.folder/name).write_bytes(jpeg)
                    elif kind=='finish':
                        summary=dict(payload,spatial_frames_written=frame_count,spatial_bytes_written=written_bytes,
                                     storage_format='lossless uncompressed decoded NPZ per frame; NOT SDK raw')
                        complete=(summary['rows']==frame_count and not summary.get('error') and summary['reason'] not in ('interrupted_incomplete',))
                        summary['archive_complete']=complete
                        json_write(self.folder/'summary.json',summary)
                        for f in handles:f.flush();f.close()
                        handles=[];json_write(self.folder/'recording_status.json',{'complete':complete,'reason':summary['reason'],'frames_written':frame_count});self.last_summary=summary;self.folder=None
                finally:
                    with self.guard:self.pending_bytes-=size
        except Exception as exc:
            self.failed=True
            if self.folder is not None:
                try:json_write(self.folder/'recording_status.json',{'complete':False,'reason':str(exc)})
                except OSError:pass
            self.fail('Recording failed: '+str(exc))
        finally:
            for f in handles:f.close()
    def close(self):
        if self.thread.is_alive():
            try:self.q.put(None,timeout=5)
            except queue.Full:self.fail('Writer could not drain; trial incomplete');return
            self.thread.join(timeout=30)
            if self.thread.is_alive():self.fail('Writer shutdown timeout; trial incomplete')
