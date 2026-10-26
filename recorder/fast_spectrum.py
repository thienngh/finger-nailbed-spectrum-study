#!/usr/bin/env python3
"""Target-rate fingertip spectra with trial markers; no perfusion/SpO2 calculation."""
import argparse
import copy
import csv
import io
import json
import platform
import queue
import threading
import time
import traceback
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
from PIL import Image
from lo_ratio import ratio_map, band_details
from spatial_recording import (Recorder, PointReference, CONDITIONS, PRESSURE_CONDITIONS,
                               spatial_masks, centered_xy, safe_json)
from study_session import Study, validate_labels

CALIBRATION = '/datastore/lo/share/calibrations/1002241528/cal-zoom-38-1002241528-20250704-133438-000000'
ACTIVE = ('baseline', 'ready', 'pressure', 'recovery')

def utc():
    return datetime.now(timezone.utc).isoformat()

def array(x):
    return np.asarray(x.get() if hasattr(x, 'get') else x)

def clean(x):
    if isinstance(x, np.ndarray):
        return clean(x.tolist())
    if isinstance(x, np.generic):
        return clean(x.item())
    if isinstance(x, float) and not np.isfinite(x):
        return None
    if isinstance(x, dict):
        return {str(k): clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    return x

def rate(times):
    if len(times) < 2 or times[-1] <= times[0]:
        return 0.0
    return (len(times)-1)/(times[-1]-times[0])

def select(spectra, coordinates, shape, roi):
    if roi is None:
        return None, np.array([], dtype=int)
    h, w = shape[:2]
    x0, y0, x1, y1 = np.asarray(roi)*[w, h, w, h]
    indices = np.flatnonzero((coordinates[:, 0] >= y0) & (coordinates[:, 0] < y1)
                            & (coordinates[:, 1] >= x0) & (coordinates[:, 1] < x1))
    return (np.nanmean(spectra[indices], axis=0) if len(indices) else None), indices

class App:
    def __init__(self, args):
        self.args = args
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.error = None
        self.started = time.monotonic()
        self.roi = None
        self.indices = np.array([], dtype=int)
        self.shape = None
        self.coordinates = None
        self.waves = None
        self.latest = None
        self.jpeg = b''
        self.preview_pending = None
        self.history = deque(maxlen=900)
        self.acquired_times = deque(maxlen=150)
        self.decoded_times = deque(maxlen=150)
        self.acquired = self.decoded = self.dropped = 0
        self.decode_ms = 0.0
        self.settings = {}
        self.stage = 'idle'
        self.baseline = None
        self.valid = None
        self.baseline_samples = []
        self.baseline_times = []
        self.events = []
        self.trial_start = None
        self.release = None
        self.folder = None
        self.trial_times = []
        self.trial_drops = 0
        self.trial_type = 'pressure_release'
        self.white = None
        self.point_refs = {'dark': None, 'white': None}
        self.point_collect = None
        self.exclusions = []
        self.artifact_marked = False
        self.frame_geometry = None
        self.trial_info = {}
        self.usable_count = 0
        self.pressure_start = None
        self.intended_pressure_seconds = args.pressure_seconds
        self.white_collect = None
        self.white_message = 'No white reference: LO-style ratio is unavailable.'
        self.index_latest = None
        self.index_count = 0
        self.index_baseline = None
        self.index_samples = []
        self.index_history = deque(maxlen=900)
        self.record = Recorder(self.fail, getattr(args,'writer_mib',256), getattr(args,'min_free_gb',2))
        self.study = Study(args.output, getattr(args,'study_id',datetime.now().strftime('study_%Y%m%d')),
                           getattr(args,'retest_seconds',900))

    def fail(self, message):
        with self.lock:
            self.error = message
        self.stop.set()

    def elapsed(self):
        return time.monotonic()-self.started

    def event(self, name, detail=''):
        t = self.elapsed()
        self.events.append((t, name))
        self.record.put(('event', [t, utc(), name, detail]))
        return t

    def command(self, action, payload):
        with self.lock:
            if self.error:
                raise ValueError(self.error)
            if self.white_collect is not None or self.point_collect is not None:
                raise ValueError('Wait until reference capture finishes.')
            if action == 'exclusions':
                if not isinstance(payload, dict): raise ValueError('Expected exclusion list')
                boxes = payload.get('rectangles', [])
                if len(boxes) > 20: raise ValueError('At most 20 exclusion rectangles')
                for box in boxes:
                    r = np.asarray(box, dtype=float)
                    if r.shape != (4,) or not np.isfinite(r).all() or (r<0).any() or (r>1).any() or r[0]>=r[2] or r[1]>=r[3]:
                        raise ValueError('Invalid exclusion rectangle')
                self.exclusions = [list(box) for box in boxes]
                if self.stage in ACTIVE: self.event('mask_changed', json.dumps(self.exclusions))
            elif action == 'artifact':
                if self.stage not in ACTIVE: raise ValueError('No active trial')
                self.artifact_marked = bool(payload.get('active', True))
                self.event('artifact_on' if self.artifact_marked else 'artifact_off')
            elif action in ('dark', 'clear_dark'):
                if self.stage in ACTIVE: raise ValueError('Finish trial before changing references')
                self.point_refs = {'dark': None, 'white': None}
                self.white = None
                self.index_latest = self.index_baseline = None
                self.index_history.clear()
                if action == 'dark': self.begin_point_reference('dark',payload)
                else: self.white_message = 'Dark and white references cleared.'
            elif action == 'roi':
                if self.stage in ACTIVE:
                    raise ValueError('Finish or abort the trial before changing the region.')
                r = np.asarray(payload, dtype=float)
                if r.shape != (4,) or not np.isfinite(r).all() or (r < 0).any() or (r > 1).any() or r[0] >= r[2] or r[1] >= r[3]:
                    raise ValueError('Drag a nonempty rectangle inside the image.')
                self.roi = r
                self.baseline = self.valid = None
                self.history.clear()
                self.latest = None
                self.indices = np.array([], dtype=int)
                self.index_baseline = None
                self.index_history.clear()
            elif action in ('white', 'clear_white'):
                if self.stage in ACTIVE:
                    raise ValueError('Finish the trial before changing the white reference.')
                self.white = None
                self.point_refs['white'] = None
                self.index_baseline = self.index_latest = None
                self.index_history.clear()
                if action == 'white':
                    if self.latest is None or not len(self.indices):
                        raise ValueError('Select an ROI on the white target first.')
                    self.begin_point_reference('white',payload)
                    self.white_collect = dict(start=self.point_collect.start, samples=[], times=[], roi=self.roi.copy())
                    self.white_message = 'Collecting white reference; keep target, light and settings fixed.'
                else:
                    self.white_message = 'White reference cleared; ratio disabled.'
            elif action == 'start':
                if self.stage in ACTIVE:
                    raise ValueError('A trial is already running.')
                if self.record.folder is not None or self.record.pending_bytes or not self.record.q.empty():
                    raise ValueError('Wait until the previous archive/reference finishes saving.')
                if self.latest is None or not len(self.indices):
                    raise ValueError('Select a region containing spectral samples first.')
                self.trial_type = payload.get('type', 'pressure_release')
                if self.trial_type not in CONDITIONS:
                    raise ValueError('Unknown trial type')
                distance = payload.get('distance_cm')
                if distance is not None and (not isinstance(distance,(float,int)) or not np.isfinite(distance) or distance <= 0):
                    raise ValueError('Camera distance must be positive cm, or omitted')
                participant, arm, site, round_number = validate_labels(payload)
                self.study.check_rest(participant, arm, self.trial_type)
                self.trial_info = {k: payload.get(k) for k in ('participant_id','arm','site','round','distance_cm','lighting','pressing_surface','force_notes','skin_tone_monk','skin_tone_fenty','fitzpatrick','ita','melanin_index','notes')}
                self.trial_info.update(participant_id=participant,arm=arm,site=site,round=round_number,roi_confirmed=True)
                intended = payload.get('planned_pressure_seconds')
                if intended is None and self.trial_type not in ('arterial_occlusion','venous_occlusion'):
                    intended = self.args.pressure_seconds
                if self.trial_type in PRESSURE_CONDITIONS and (not isinstance(intended,(int,float)) or not np.isfinite(intended) or intended<=0):
                    raise ValueError('Enter the clinician-approved pressure duration in seconds (reminder only)')
                self.intended_pressure_seconds = intended
                self.trial_info['planned_pressure_seconds'] = intended
                for key,value in self.trial_info.items():
                    if value is not None and len(str(value))>500: raise ValueError('Metadata field too long: '+key)
                if getattr(self.args,'require_references',False) and not all(self.point_refs.values()):
                    raise ValueError('Capture per-point dark AND white references before starting')
                self.frame_geometry = (self.coordinates.copy(), self.waves.copy(), tuple(self.shape))
                self.artifact_marked = False
                self.pressure_start = None
                self.folder = self.study.new_folder(participant,arm,site,self.trial_type)
                self.record.last_summary = None
                self.baseline = self.valid = None
                self.baseline_samples = []; self.baseline_times = []
                self.history.clear(); self.events = []; self.trial_times = []
                self.trial_drops = self.dropped
                self.release = None
                self.index_baseline = None
                self.index_samples = []
                self.index_history.clear()
                config = dict(mode='SIMULATION' if self.args.simulate else 'LIVE',
                              target_hz=self.args.fps, settings=self.settings, calibration=self.args.calibration,
                              roi_normalized=self.roi, sample_indices=self.indices,
                              sampling_coordinates_yx=self.coordinates[self.indices], image_shape=self.shape,
                              wavelengths_nm=self.waves, trial_type=self.trial_type,
                              baseline_seconds=self.args.baseline_seconds, recovery_seconds=self.args.recovery_seconds,
                              time_basis='Host monotonic receipt time, NOT verified sensor exposure time',
                              signal_units='arbitrary units', invalid_baseline_rule='mean <= 0 or <1% of largest positive baseline band',
                              recording='All decoded spectral points and full decoded scene image every processed trial frame; no SDK encoded raw recording',
                              schema_version=2, study_id=self.study.study_id,
                              minimum_same_arm_retest_seconds=self.study.retest_seconds,
                              full_sampling_coordinates_yx=self.coordinates,
                              coordinates_convention='Original y,x image pixels; centered x right, y down, ROI center=(0,0); not mm',
                              all_point_count=len(self.coordinates), trial_info=self.trial_info,
                              point_references=self.point_refs, exclusion_rectangles=self.exclusions,
                              shadow_margin_px=getattr(self.args,'shadow_margin_px',10),
                              masking='Manual rectangles + margin, nonfinite points and operator artifact flag; NOT automatic clamp/shadow detection',
                              intended_pressure_seconds=self.intended_pressure_seconds,
                              field_calibration='Original spectrum CSV remains factory-decoded arbitrary units',
                              white_reference=self.white,
                              lo_ratio=dict(enabled=self.white is not None, requested_nm=[548,599],
                                            actual_samples_nm=band_details(self.waves),
                                            white_conversion='spectra / averaged white spectrum; vendor helper unavailable',
                                            preprocessing='Per-point L2 then full-frame min/max; matches pasted normalization',
                                            invalid_rule='Exclude negative/nonfinite rows, zero log inputs and denominator <=1e-12',
                                            aggregation='Mean of valid point ratios within ROI; not ratio of mean spectrum'))
                self.record.put(('start', (self.folder, self.waves.copy(), config)))
                self.stage = 'baseline'
                self.trial_start = self.event('baseline_started')
            elif action == 'pressure':
                if self.trial_type == 'resting':
                    raise ValueError('Resting trials have no pressure marker; finish and select the correct condition.')
                if self.stage != 'ready':
                    raise ValueError('Wait for baseline completion first.')
                if self.trial_type in PRESSURE_CONDITIONS:
                    self.study.check_rest(self.trial_info['participant_id'],self.trial_info['arm'],self.trial_type)
                    self.study.intervention(self.trial_info['participant_id'],self.trial_info['arm'],'pressure_started',self.folder)
                self.pressure_start = self.event('pressure_started' if self.trial_type in PRESSURE_CONDITIONS else 'control_action_started')
                self.stage = 'pressure'
            elif action == 'release':
                if self.stage != 'pressure':
                    raise ValueError('Mark pressure/control start first.')
                if self.trial_type in PRESSURE_CONDITIONS:
                    self.study.intervention(self.trial_info['participant_id'],self.trial_info['arm'],'released',self.folder)
                self.release = self.event('released')
                self.stage = 'recovery'
            elif action == 'finish':
                if self.stage not in ACTIVE:
                    raise ValueError('No active trial.')
                self.finish('manually_finished')
            else:
                raise ValueError('Unknown action')

    def begin_point_reference(self, kind, context=None):
        if self.coordinates is None or self.roi is None:
            raise ValueError('Wait for frames and select a reference ROI first')
        if context is not None and (not isinstance(context,dict) or len(json.dumps(context))>4096):
            raise ValueError('Reference context must be a short metadata object')
        self.point_collect = PointReference(kind,self.elapsed(),self.coordinates,self.waves,self.settings,self.roi)
        self.point_collect.context = context or {}
        self.white_message = ('Capturing '+kind+' reference. Keep settings and geometry fixed; dark: cover lens, white: target fills field.')

    def update_point_reference(self, spectra, image, waves, coords, received):
        cap = self.point_collect
        if cap is None: return
        if not np.array_equal(coords,cap.coords) or not np.array_equal(waves,cap.waves) or self.settings != cap.settings:
            raise ValueError('Reference geometry/settings changed; recapture')
        cap.add(spectra,array(image),received)
        if received-cap.start < self.args.white_seconds: return
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
        path=self.study.root/'references'/(cap.kind+'_'+stamp+'_points.npz')
        content=cap.content()
        meta=dict(kind=cap.kind,path=str(path.resolve()),frames=cap.count,captured_utc=utc(),
                  settings=cap.settings, wavelengths_nm=cap.waves, optical_context=cap.context,
                  meaning='Per-point decoded signals; white standard assumed unit reflectance; no certified absolute calibration')
        self.record.put(('point_reference',(path,content,meta)))
        self.point_refs[cap.kind]=safe_json(meta)
        self.point_collect=None
        if cap.kind=='white':
            _,_,usable=spatial_masks(cap.mean,coords,self.shape,cap.roi)
            mean=np.mean(cap.mean[usable],axis=0)
            self.white=dict(mode='SIMULATION' if self.args.simulate else 'LIVE',captured_utc=utc(),
                            wavelengths_nm=waves.copy(),mean_signal=mean,frames=cap.count,
                            roi_normalized=cap.roi,settings=dict(self.settings),path=str(path),dark_correction=False)
            if not np.isfinite(mean).all() or np.any(mean<=0): self.white=None
            self.white_collect=None
        self.white_message=cap.kind.capitalize()+' reference captured. Dark + white available: '+str(all(self.point_refs.values()))

    def finish(self, reason):
        if self.pressure_start is not None and self.release is None and self.trial_type in PRESSURE_CONDITIONS:
            self.study.intervention(self.trial_info['participant_id'],self.trial_info['arm'],
                                    'finished_without_release_marker',self.folder)
        self.event(reason)
        times = self.trial_times
        summary = dict(reason=reason, rows=len(times), achieved_roi_hz=rate(times),
                       max_receipt_gap_s=max(np.diff(times)) if len(times)>1 else None,
                       raw_frames_dropped_before_decode=self.dropped-self.trial_drops,
                       error=self.error, time_basis='host receipt', target_hz=self.args.fps,
                       baseline_frames=len(self.baseline_samples))
        summary['lo_ratio_baseline'] = self.index_baseline
        summary['lo_ratio_baseline_valid_frames'] = len(self.index_samples)
        self.record.put(('finish', summary))
        self.stage = 'done'

    def process(self, seq, received, received_utc, metadata, image, spectra, waves, coords, decode_ms):
        # Numeric ROI extraction is fast; image processing stays in another thread.
        spectra, waves = array(spectra), array(waves)
        if spectra.shape != (len(coords), len(waves)):
            raise ValueError('Decoded spectra/coordinates/wavelength shapes disagree')
        with self.lock:
            if self.waves is not None and not np.array_equal(self.waves, waves):
                raise ValueError('Wavelength calibration changed during acquisition')
            if any(self.point_refs.values()) and self.coordinates is not None:
                changed=not np.array_equal(coords,self.coordinates) or tuple(image.shape)!=tuple(self.shape)
                changed=changed or any(ref and ref['settings']!=self.settings for ref in self.point_refs.values())
                if changed:
                    self.point_refs={'dark':None,'white':None};self.white=None
                    self.white_message='Geometry/settings changed. Recapture dark and white.'
            self.waves = waves.copy()
            self.coordinates = np.asarray(coords)
            self.shape = image.shape
            roi_mask, exclude_mask, usable_mask = spatial_masks(spectra,coords,image.shape,self.roi,self.exclusions,getattr(self.args,'shadow_margin_px',10))
            if self.artifact_marked: usable_mask[:] = False
            indices = np.flatnonzero(usable_mask)
            values = spectra[indices].mean(axis=0) if len(indices)>=getattr(self.args,'min_points',10) else None
            self.usable_count = len(indices)
            self.indices = indices
            self.latest = values
            self.decoded += 1
            self.decoded_times.append(received)
            self.decode_ms = decode_ms
            self.index_latest = None
            self.index_count = 0
            norm_limits = (None, None)
            if self.white is not None and len(indices):
                ratios, norm_limits = ratio_map(spectra, waves, self.white['mean_signal'])
                selected = ratios[indices]
                good = np.isfinite(selected)
                self.index_count = int(good.sum())
                if self.index_count:
                    self.index_latest = float(selected[good].mean())
                self.index_history.append((received, self.index_latest))
            if values is not None:
                self.history.append((received, values.copy()))
            elif self.stage in ACTIVE:
                self.history.append((received,np.full(len(waves),np.nan)))
            if self.preview_pending is None:
                self.preview_pending = (seq, received, array(image[::4, ::4]).copy())
            self.update_point_reference(spectra,image,waves,coords,received)
            if self.stage not in ACTIVE or received < self.trial_start:
                return
            # Preserve the full frame even if the selected tissue is unusable.
            if self.frame_geometry is not None:
                cc,ww,ss=self.frame_geometry
                if not np.array_equal(coords,cc) or not np.array_equal(waves,ww) or tuple(image.shape)!=ss:
                    raise ValueError('Trial geometry changed; recording incomplete')
            if values is None: values = np.full(len(waves),np.nan)
            # Mark phases by receipt timestamps, not by when a queued frame is decoded.
            phase = 'baseline'
            for event_t, name in self.events:
                if event_t <= received:
                    if name == 'baseline_complete': phase = 'ready'
                    elif name in ('pressure_started', 'control_action_started'): phase = 'pressure'
                    elif name == 'released': phase = 'recovery'
            if self.stage == 'baseline' and np.isfinite(values).all():
                self.baseline_samples.append(values.copy())
                self.baseline_times.append(received)
                if self.index_latest is not None:
                    self.index_samples.append(self.index_latest)
                if received-self.trial_start >= self.args.baseline_seconds:
                    if len(self.baseline_samples) < 10:
                        raise ValueError('Fewer than 10 baseline spectra; acquisition is too slow.')
                    b = np.stack(self.baseline_samples)
                    self.baseline = b.mean(axis=0)
                    self.valid = np.isfinite(self.baseline) & (self.baseline > max(0., float(np.max(self.baseline))*.01))
                    self.record.put(('baseline', (waves.copy(), self.baseline.copy(), b.std(axis=0, ddof=1), len(b), self.baseline_times[-1]-self.baseline_times[0])))
                    if self.index_samples:
                        self.index_baseline = float(np.mean(self.index_samples))
                    self.record.put(('index_baseline', dict(mean=self.index_baseline,
                                                          valid_frames=len(self.index_samples),
                                                          total_baseline_frames=len(b))))
                    self.event('baseline_complete', 'Averaged %d frames' % len(b))
                    self.stage = 'ready'
            if self.stage == 'baseline' and received-self.trial_start > self.args.baseline_seconds+5:
                raise ValueError('Insufficient usable baseline samples; fix ROI/masks and repeat')
            peak = float(waves[np.argmax(values)]) if np.isfinite(values).all() else None
            fields = {}
            if metadata is not None:
                try:
                    for key, value in vars(metadata).items():
                        if 'time' in key.lower():
                            fields[key] = repr(value)[:300]
                except TypeError:
                    pass
            metadata_record = safe_json(vars(metadata)) if metadata is not None and hasattr(metadata,'__dict__') else {'repr':repr(metadata)}
            content=dict(spectra=spectra.copy(),scene_image=array(image).copy(),
                         coordinates_yx=np.array(coords,copy=True),coordinates_xy_centered=centered_xy(coords,image.shape,self.roi),
                         wavelengths_nm=waves.copy(),roi_normalized=self.roi.copy(),roi_mask=roi_mask.copy(),
                         exclusion_mask=exclude_mask.copy(),usable_mask=usable_mask.copy(),
                         artifact_marked=np.array(self.artifact_marked),frame=np.array(seq),
                         host_received_s=np.array(received),host_received_utc=np.array(received_utc),
                         decoded_s=np.array(self.elapsed()),trial_s=np.array(received-self.trial_start),
                         phase=np.array(phase),metadata_json=np.array(json.dumps(metadata_record,allow_nan=False)),
                         exclusion_rectangles=np.asarray(self.exclusions,dtype=float).reshape(-1,4),
                         shadow_margin_px=np.array(getattr(self.args,'shadow_margin_px',10)))
            self.record.put(('frame',(seq,content)))
            self.record.put(('row', [seq, received, received_utc, self.elapsed(), received-self.trial_start,
                                     phase, len(indices), peak, json.dumps(fields)] + values.tolist()))
            relative = None
            if self.index_latest is not None and self.index_baseline is not None and abs(self.index_baseline) > 1e-12:
                relative = 100*(self.index_latest/self.index_baseline-1)
            self.record.put(('index', [seq, received, received-self.trial_start, phase,
                                       self.index_latest, self.index_count, len(indices), relative] + list(norm_limits)))
            self.trial_times.append(received)
            if self.stage == 'recovery' and received-self.release >= self.args.recovery_seconds:
                self.finish('recovery_completed')

    def snapshot(self):
        with self.lock:
            recent = list(self.history)[-300:]
            if recent:
                recent = [item for item in recent if item[0] >= recent[-1][0]-10]
            wave = self.waves
            baseline = self.baseline
            heat = []
            # Keep every recent sample so the map preserves actual acquisition gaps.
            if baseline is not None:
                for t, values in recent:
                    change = np.full_like(values, np.nan)
                    change[self.valid] = 100*(values[self.valid]/baseline[self.valid]-1)
                    heat.append([t, change])
            return clean(dict(mode='SIMULATION' if self.args.simulate else 'LIVE', error=self.error,
                              study=self.study.snapshot(), trial_info=self.trial_info,
                              target_hz=self.args.fps, acquired_hz=rate(self.acquired_times),
                              decoded_hz=rate(self.decoded_times), decode_ms=self.decode_ms,
                              dropped=self.dropped, acquired=self.acquired, decoded=self.decoded,
                              archive_complete=self.record.last_summary.get('archive_complete') if self.record.last_summary else None,
                              writer_queue=self.record.q.qsize(), stage=self.stage, trial_type=self.trial_type, now=self.elapsed(),
                              trial_start=self.trial_start, baseline_seconds=self.args.baseline_seconds,
                              recovery_seconds=self.args.recovery_seconds, release=self.release,
                              roi=self.roi, count=len(self.indices), all_point_count=len(self.coordinates) if self.coordinates is not None else 0,
                              exclusions=self.exclusions, artifact_marked=self.artifact_marked,
                              shadow_margin_px=getattr(self.args,'shadow_margin_px',10),
                              dark_ready=self.point_refs['dark'] is not None,
                              point_white_ready=self.point_refs['white'] is not None,
                              reference_collecting=self.point_collect.kind if self.point_collect else None,
                              reference_progress=self.elapsed()-self.point_collect.start if self.point_collect else None,
                              writer_pending_mib=self.record.pending_bytes/1024**2,
                              pressure_elapsed=self.elapsed()-self.pressure_start if self.stage=='pressure' and self.pressure_start is not None else None,
                              intended_pressure_seconds=self.intended_pressure_seconds, wavelengths=wave, values=self.latest,
                              baseline=baseline, valid_bands=self.valid,
                              history=[[t, v] for t, v in recent], heatmap=heat, events=self.events,
                              white_message=self.white_message, white_ready=self.white is not None,
                              white_collecting=self.point_collect is not None,
                              white_progress=(self.elapsed()-self.white_collect['start']) if self.white_collect else None,
                              white_seconds=self.args.white_seconds,
                              index_latest=self.index_latest, index_count=self.index_count,
                              index_baseline=self.index_baseline, index_baseline_frames=len(self.index_samples),
                              index_history=[[t,v] for t,v in self.index_history if t >= self.elapsed()-10],
                              folder=str(self.folder) if self.folder else None))

    def preview_worker(self):
        try:
            while not self.stop.wait(.5):
                with self.lock:
                    pending = self.preview_pending
                    self.preview_pending = None
                if pending is None:
                    continue
                seq, t, gray = pending
                if gray.ndim == 3:
                    gray = gray.mean(axis=2)
                low, high = np.nanpercentile(gray, [1, 99])
                pixels = (np.clip((gray-low)/max(float(high-low), 1e-12), 0, 1)*255).astype('uint8')
                buf = io.BytesIO(); Image.fromarray(pixels).save(buf, 'JPEG', quality=75)
                jpeg = buf.getvalue()
                with self.lock:
                    self.jpeg = jpeg
                    if self.stage in ACTIVE and t >= self.trial_start:
                        self.record.put(('preview', ('preview_%08d.jpg' % seq, jpeg)))
        except Exception as exc:
            self.fail('Preview failed: ' + str(exc))


def handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def respond(self, body, kind='application/json', status=200):
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            try: self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError): pass
        def do_GET(self):
            path = self.path.split('?')[0]
            if path == '/':
                self.respond(Path(__file__).with_name('viewer.html').read_bytes(), 'text/html; charset=utf-8')
            elif path == '/data':
                self.respond(json.dumps(app.snapshot(), allow_nan=False).encode())
            elif path == '/preview':
                with app.lock: jpeg = app.jpeg
                self.respond(jpeg, 'image/jpeg')
            else: self.send_error(404)
        def do_POST(self):
            try:
                n = int(self.headers.get('Content-Length', '0'))
                if not 0 < n < 16384: raise ValueError('Invalid request length')
                origin = self.headers.get('Origin')
                if origin and origin != 'http://' + self.headers.get('Host', ''):
                    raise ValueError('Cross-origin commands are disabled')
                payload = json.loads(self.rfile.read(n))
                app.command(self.path.strip('/'), payload)
                self.respond(b'{"ok":true}')
            except (ValueError, TypeError, AttributeError) as exc:
                self.respond(json.dumps({'error':str(exc)}).encode(), status=400)
    return Handler


def camera_worker(app, frames):
    try:
        if app.args.simulate:
            app.settings = {'source':'synthetic 1.2 Hz pulse, NOT physiology validation', 'exposure_us':int(app.args.exposure_ms*1000),'gain_sdk_units':int(app.args.gain_db*10)}
            seq = 0; deadline = time.monotonic()
            while not app.stop.is_set():
                t = app.elapsed()
                enqueue(app, frames, (seq, t, utc(), None))
                seq += 1
                deadline += 1/app.args.fps
                app.stop.wait(max(0, deadline-time.monotonic()))
        else:
            from lo.sdk.api.camera.camera import LOCamera
            with LOCamera() as cam:
                if hasattr(cam, 'disable_auto_exposure'):
                    cam.disable_auto_exposure()
                    auto = 'disabled through SDK'
                else:
                    auto = 'not controlled by this SDK API; verify independently'
                # Units verified in SDK: microhertz, microseconds, tenths of dB.
                cam.exposure = int(app.args.exposure_ms*1000)
                cam.gain = int(round(app.args.gain_db*10))
                cam.frame_rate = int(app.args.fps*1e6)
                with app.lock:
                    app.settings = dict(requested_fps=app.args.fps, exposure_us=cam.exposure,
                                        gain_sdk_units=cam.gain, frame_rate_sdk_units=cam.frame_rate,
                                        auto_exposure=auto, white_balance='No white-balance adjustment applied by this recorder; SDK control not verified')
                print('Camera settings:', app.settings, flush=True)
                for seq, frame in enumerate(cam):
                    if app.stop.is_set(): break
                    t = app.elapsed(); received_utc = utc()
                    # Own buffers before SDK recycles them; copy cost counts against throughput.
                    enqueue(app, frames, (seq, t, received_utc, copy.deepcopy(frame)))
    except Exception as exc:
        app.fail('Camera acquisition failed: ' + str(exc))
        traceback.print_exc()


def enqueue(app, frames, item):
    with app.lock:
        app.acquired += 1
        app.acquired_times.append(item[1])
    try:
        frames.put_nowait(item)
    except queue.Full:
        try: frames.get_nowait()
        except queue.Empty: pass
        with app.lock: app.dropped += 1
        if app.stage in ACTIVE:
            app.fail('Acquisition queue overflow during trial; full-frame capture incomplete. Reduce rate or improve throughput.')
            return
        frames.put_nowait(item)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fps', type=int, choices=range(1,31), default=30)
    parser.add_argument('--exposure-ms', type=float, default=10)
    parser.add_argument('--gain-db', type=float, default=0)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--calibration', default=CALIBRATION)
    parser.add_argument('--output', default='recordings_spatial')
    parser.add_argument('--study-id', default=datetime.now().strftime('study_%Y%m%d'))
    parser.add_argument('--retest-seconds',type=float,default=900,
                        help='Minimum same-arm interval after pressure/release; clinician confirms recovery')
    parser.add_argument('--baseline-seconds', type=float, default=10)
    parser.add_argument('--white-seconds', type=float, default=5)
    parser.add_argument('--recovery-seconds', type=float, default=20)
    parser.add_argument('--simulate', action='store_true')
    parser.add_argument('--simulate-full-size',action='store_true',help='Simulation: 4384 points and a 2048x2432 uint16 scene; substantial disk use')
    parser.add_argument('--writer-mib',type=float,default=256,help='Maximum queued array memory in MiB')
    parser.add_argument('--min-free-gb',type=float,default=2,help='Stop if free disk falls below this GiB')
    parser.add_argument('--shadow-margin-px',type=float,default=10,help='Expand each manual exclusion by image pixels')
    parser.add_argument('--min-points',type=int,default=10)
    parser.add_argument('--pressure-seconds',type=float,default=10,help='Reminder only; never controls force or release')
    parser.add_argument('--require-references',action='store_true',help='Require both per-point dark and white before trials')
    args = parser.parse_args()
    if not all(np.isfinite(v) for v in [args.retest_seconds,args.writer_mib,args.min_free_gb,args.shadow_margin_px,args.pressure_seconds,args.exposure_ms,args.gain_db,args.baseline_seconds,args.white_seconds,args.recovery_seconds]):
        parser.error('Numeric parameters must be finite')
    if args.retest_seconds<0 or (not args.simulate and args.retest_seconds<900):
        parser.error('Live collection requires at least 900 seconds between same-arm pressure trials')
    if args.writer_mib<=0 or args.min_free_gb<0 or args.shadow_margin_px<0 or args.min_points<1 or args.pressure_seconds<=0:
        parser.error('Invalid storage, mask or reminder settings')
    if not 0 < args.exposure_ms < 1000/args.fps:
        parser.error('Exposure must be positive and shorter than one frame period.')
    if args.baseline_seconds <= 0 or args.recovery_seconds <= 0 or args.white_seconds <= 0:
        parser.error('Trial durations must be positive')
    if not args.simulate:
        modelpath = Path('/proc/device-tree/model')
        model = modelpath.read_text(errors='replace') if modelpath.exists() else ''
        if platform.system() != 'Linux' or 'Orin' not in model:
            parser.error('Live mode must run on the Jetson Orin. Use --simulate on the Mac.')
        from lo.sdk.api.acquisition.data.decode import SpectralDecoder
        decode = SpectralDecoder.from_calibration(args.calibration)
        coords = array(decode.sampling_coordinates)
    else:
        if args.simulate_full_size:
            y,x=np.meshgrid(np.linspace(20,2028,32),np.linspace(20,2412,137),indexing='ij')
        else:
            y, x = np.mgrid[12:240:12, 12:320:12]
        coords = np.column_stack([y.ravel(), x.ravel()])
    app = App(args)
    frames = queue.Queue(4)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler(app))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    capture = threading.Thread(target=camera_worker, args=(app,frames), daemon=True)
    preview = threading.Thread(target=app.preview_worker, daemon=True)
    capture.start(); preview.start()
    print('Open http://127.0.0.1:%d (forward this port from Jetson). Select ROI, then Start trial.' % args.port, flush=True)
    try:
        while not app.stop.is_set():
            try: seq, received, received_utc, raw = frames.get(timeout=.5)
            except queue.Empty: continue
            begun = time.monotonic()
            if args.simulate:
                waves = np.linspace(440,900,96)
                signal = .7+np.exp(-((waves-600)/80)**2)
                sensitivity = np.exp(-((waves-550)/40)**2)
                spectrum = signal*(1+.015*sensitivity*np.sin(2*np.pi*1.2*received))
                spectra = np.tile(spectrum,(len(coords),1))*(.9+.2*coords[:,1:2]/max(coords[:,1]))
                if app.point_collect is not None:
                    if app.point_collect.kind=='dark':spectra[:]=.01
                    else:spectra*=2
                if args.simulate_full_size:
                    image=np.broadcast_to(np.arange(2432,dtype=np.uint16),(2048,2432))
                else:image=np.broadcast_to(np.arange(320,dtype=np.uint16),(240,320))
                metadata = None
            else:
                metadata, image, spectra = decode(raw)
                waves = metadata.wavelengths
            app.process(seq, received, received_utc, metadata, image, spectra, waves, coords,
                        1000*(time.monotonic()-begun))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        app.fail('Processing failed: '+str(exc))
        traceback.print_exc()
    finally:
        app.stop.set()
        capture.join(timeout=5); preview.join(timeout=2)
        with app.lock:
            if app.stage in ACTIVE:
                try: app.finish('interrupted_incomplete')
                except RuntimeError: pass  # Writer already reports incomplete state on disk.
        app.record.close()
        try:
            app.study.write_inventory()
        except OSError as exc:
            print('Could not write study manifest:',exc,flush=True)
        print('Decoded:',app.decoded,'Dropped before decode:',app.dropped,'Error:',app.error,flush=True)
        server.shutdown(); server.server_close()
    if app.error:
        raise SystemExit(1)

if __name__ == '__main__':
    main()
