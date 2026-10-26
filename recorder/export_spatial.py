#!/usr/bin/env python3
"""Review/re-mask decoded frames and export original-rate spectra or all points.

No averaging in time, no interpolation, no automatic clamp identification.
Annotations use normalized IMAGE coordinates, not preview pixels or centered mm.
"""
import argparse
import csv
import gzip
import json
import shutil
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from spatial_recording import spatial_masks,centered_xy,relative_reflectance,safe_json,json_write


def export(session,output,annotations=None,points_csv=False,reflectance=False,review=False,min_points=10,common_points=False):
    session=Path(session);output=Path(output)
    if session.resolve()==output.resolve():raise ValueError('Use a separate derived output directory')
    output.mkdir(parents=True,exist_ok=False)
    config=json.loads((session/'session.json').read_text())
    if config.get('schema_version')!=2:raise ValueError('This recording has no saved spatial frames; old ROI CSVs cannot be expanded')
    source_status=json.loads((session/'recording_status.json').read_text())
    records=list(csv.DictReader((session/'frames.csv').open()))
    annotations=annotations or {'ranges':[]}
    for region in annotations.get('ranges',[]):
        if int(region['start_frame'])>int(region['end_frame']):raise ValueError('Annotation start exceeds end')
        for box in region.get('rectangles',[]):
            if len(box)!=4 or not np.isfinite(box).all() or min(box)<0 or max(box)>1 or box[0]>=box[2] or box[1]>=box[3]:raise ValueError('Invalid normalized annotation box')
        margin=float(region.get('margin_px',10))
        if not np.isfinite(margin) or margin<0:raise ValueError('Invalid shadow margin')
    common_mask=None
    if common_points:
        # Intersection across non-artifact frames prevents a moving mask from
        # changing the set of tissue locations in the exported time trace.
        for record in records:
            path=(session/record['path']).resolve()
            if session.resolve() not in path.parents:raise ValueError('Unsafe frame path')
            with np.load(path,allow_pickle=False) as z:
                seq=int(z['frame']);artifact=bool(z['artifact_marked'])
                good=z['roi_mask']&~z['exclusion_mask']&np.isfinite(z['spectra']).all(axis=1)
                for ann in annotations.get('ranges',[]):
                    if int(ann['start_frame'])<=seq<=int(ann['end_frame']):
                        _,bad,_=spatial_masks(z['spectra'],z['coordinates_yx'],config['image_shape'],z['roi_normalized'],ann.get('rectangles',[]),float(ann.get('margin_px',10)))
                        good &= ~bad;artifact |= bool(ann.get('artifact',False))
                if not artifact:common_mask=good.copy() if common_mask is None else common_mask&good
        if common_mask is None or int(common_mask.sum())<min_points:
            raise ValueError('Too few clean points shared by all non-artifact frames; revise annotations or use an explicitly reviewed shorter recording')
    refs={}
    if reflectance:
        for kind in ('dark','white'):
            with np.load(session/(kind+'_points.npz'),allow_pickle=False) as z:
                refs[kind]={k:z[k].copy() for k in ('spectra_mean','coordinates_yx','wavelengths_nm')}
    output_rows=[];review_rows=[];point_file=None;writer=None;total_points=0
    try:
        if points_csv:
            point_file=gzip.open(output/'point_spectra.csv.gz','wt',newline='')
            writer=csv.writer(point_file)
        with (output/'frame_spectra.csv').open('w',newline='') as file:
            fw=csv.writer(file)
            for count,record in enumerate(records):
                path=(session/record['path']).resolve()
                if session.resolve() not in path.parents:raise ValueError('Unsafe frame path')
                with np.load(path,allow_pickle=False) as z:
                    spectra=z['spectra'];coords=z['coordinates_yx'];waves=z['wavelengths_nm'];scene=z['scene_image'];roi=z['roi_normalized']
                    seq=int(z['frame']);boxes=z['exclusion_rectangles'].tolist();margin=float(z['shadow_margin_px'])
                    roi_mask,excluded,usable=spatial_masks(spectra,coords,scene.shape,roi,boxes,margin)
                    artifact=bool(z['artifact_marked'])
                    for ann in annotations.get('ranges',[]):
                        if int(ann['start_frame'])<=seq<=int(ann['end_frame']):
                            _,bad,_=spatial_masks(spectra,coords,scene.shape,roi,ann.get('rectangles',[]),float(ann.get('margin_px',10)))
                            excluded |= bad
                            artifact |= bool(ann.get('artifact',False))
                    usable=roi_mask&~excluded&np.isfinite(spectra).all(axis=1)
                    if common_mask is not None:usable &= common_mask
                    if artifact:usable[:]=False
                    n=int(usable.sum());values=np.full(len(waves),np.nan)
                    if n>=min_points:values=spectra[usable].mean(axis=0)
                    common=[seq,float(z['host_received_s']),str(z['host_received_utc']),float(z['trial_s']),str(z['phase']),n,artifact,n>=min_points]
                    corrected=np.full(len(waves),np.nan);valid_counts=np.zeros(len(waves),dtype=int)
                    if reflectance:
                        for ref in refs.values():
                            if not np.array_equal(ref['coordinates_yx'],coords) or not np.array_equal(ref['wavelengths_nm'],waves):raise ValueError('Reference geometry mismatch')
                        rr,valid=relative_reflectance(spectra,refs['dark']['spectra_mean'],refs['white']['spectra_mean'])
                        valid &= usable[:,None];valid_counts=valid.sum(axis=0)
                        for j in range(len(waves)):
                            if valid_counts[j]>=min_points:corrected[j]=rr[valid[:,j],j].mean()
                    if count==0:
                        header=['frame','host_received_s','host_received_utc','trial_s','phase','usable_points','artifact_marked','usable_frame']
                        header+=['%.5f_nm_au'%w for w in waves]
                        if reflectance:header+=['%.5f_nm_relative_reflectance'%w for w in waves]+['reference_valid_counts_json']
                        fw.writerow(header)
                        if writer:
                            writer.writerow(['frame','host_received_s','host_received_utc','trial_s','point_id','x_image_px','y_image_px','x_centered_px','y_centered_px','in_roi','excluded','usable']+['%.5f_nm_au'%w for w in waves])
                    fw.writerow(common+values.tolist()+(corrected.tolist()+[json.dumps(valid_counts.tolist())] if reflectance else []))
                    if writer:
                        xy=centered_xy(coords,scene.shape,roi)
                        for k in range(len(spectra)):
                            writer.writerow([seq,*common[1:4],k,coords[k,1],coords[k,0],xy[k,0],xy[k,1],bool(roi_mask[k]),bool(excluded[k]),bool(usable[k])]+spectra[k].tolist())
                        total_points+=len(spectra)
                    output_rows.append((common,values))
                    if review:
                        gray=scene.mean(axis=2) if scene.ndim==3 else scene
                        lo,hi=np.nanpercentile(gray,[1,99]);pixels=(np.clip((gray-lo)/max(float(hi-lo),1e-12),0,1)*255).astype('uint8')
                        im=Image.fromarray(pixels).convert('RGB');draw=ImageDraw.Draw(im);h,w=scene.shape[:2]
                        draw.rectangle((roi[0]*w,roi[1]*h,roi[2]*w,roi[3]*h),outline='yellow',width=3)
                        im.thumbnail((960,960));name='review_%09d.jpg'%seq;im.save(output/name,quality=85)
                        review_rows.append(dict(frame=seq,trial_s=common[3],path=name))
        shutil.copyfile(session/'events.csv',output/'events.csv')
        json_write(output/'annotations_used.json',annotations)
        json_write(output/'export_summary.json',dict(source=str(session.resolve()),source_status=source_status,frames=len(records),point_rows=total_points,
                   minimum_usable_points=min_points,reflectance_applied=reflectance,temporal_averaging=False,
                   common_point_ids=np.flatnonzero(common_mask).tolist() if common_mask is not None else None,
                   notes='Masks are explicit operator annotations, not validated clamp/shadow classification. Different usable points may contribute between frames. White assumed unit reflectance.'))
        if review:write_review(output,review_rows)
    finally:
        if point_file:point_file.close()
    return output


def write_review(output,rows):
    template='''<!doctype html><meta charset="utf-8"><title>Spatial frame review</title><style>body{font:16px system-ui;max-width:1000px;margin:30px auto}canvas{max-width:100%;touch-action:none;border:1px solid #555}button,input{font:inherit;margin:6px;padding:6px}</style><h1>Frame-by-frame spatial review</h1><p>Yellow: original ROI. Drag red boxes to exclude clamp/shadow. Mask coordinates are normalized to the full image. Review every relevant frame; no automatic tracking. Download annotations, then rerun the exporter with --annotations. Original data are unchanged.</p><input id="slider" type="range" min="0" value="0"><button id="prev">Previous</button><button id="next">Next</button><p id="info"></p><canvas id="canvas"></canvas><div><label>Apply from frame <input id="start" type="number"></label><label>through frame <input id="end" type="number"></label><label>Shadow margin (original image px) <input id="margin" type="number" value="10" min="0"></label></div><button id="add">Save exclusion boxes for range</button><button id="bad">Mark whole range unusable</button><button id="clear">Clear drawn boxes</button><button id="download">Download annotations</button><p id="count"></p><script>
const frames=__FRAMES__,q=s=>document.querySelector(s),canvas=q('#canvas'),ctx=canvas.getContext('2d');let image=new Image(),boxes=[],origin=null,ranges=[];q('#slider').max=frames.length-1;
function show(){let f=frames[Number(q('#slider').value)];boxes=[];q('#info').textContent=`Frame ${f.frame} · trial ${f.trial_s.toFixed(4)} s`;q('#start').value=q('#end').value=f.frame;image.onload=()=>{canvas.width=image.width;canvas.height=image.height;draw()};image.src=f.path}
function draw(){ctx.drawImage(image,0,0);ctx.strokeStyle='red';ctx.lineWidth=2;for(let b of boxes)ctx.strokeRect(b[0]*canvas.width,b[1]*canvas.height,(b[2]-b[0])*canvas.width,(b[3]-b[1])*canvas.height)}
function xy(e){let r=canvas.getBoundingClientRect();return [(e.clientX-r.left)/r.width,(e.clientY-r.top)/r.height].map(x=>Math.max(0,Math.min(1,x)))}
canvas.onpointerdown=e=>{origin=xy(e);canvas.setPointerCapture(e.pointerId)};canvas.onpointerup=e=>{if(!origin)return;let end=xy(e);boxes.push([Math.min(origin[0],end[0]),Math.min(origin[1],end[1]),Math.max(origin[0],end[0]),Math.max(origin[1],end[1])]);origin=null;draw()};
q('#slider').oninput=show;q('#prev').onclick=()=>{q('#slider').value=Math.max(0,Number(q('#slider').value)-1);show()};q('#next').onclick=()=>{q('#slider').value=Math.min(frames.length-1,Number(q('#slider').value)+1);show()};q('#clear').onclick=()=>{boxes=[];draw()};
function save(artifact){let a=Number(q('#start').value),b=Number(q('#end').value),m=Number(q('#margin').value);if(!Number.isFinite(a)||!Number.isFinite(b)||a>b||m<0)return alert('Check frame range and margin');ranges.push({start_frame:a,end_frame:b,rectangles:artifact?[]:boxes.map(b=>[...b]),margin_px:m,artifact});q('#count').textContent=ranges.length+' saved annotations'}
q('#add').onclick=()=>save(false);q('#bad').onclick=()=>save(true);q('#download').onclick=()=>{let u=URL.createObjectURL(new Blob([JSON.stringify({ranges},null,2)],{type:'application/json'}));let a=document.createElement('a');a.href=u;a.download='annotations.json';a.click();URL.revokeObjectURL(u)};show();</script>'''
    (output/'review.html').write_text(template.replace('__FRAMES__',json.dumps(rows)))


def plot_export(folder):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    folder=Path(folder)
    with (folder/'frame_spectra.csv').open() as f:rows=list(csv.DictReader(f))
    cols=[k for k in rows[0] if k.endswith('_nm_au')];w=np.array([float(k[:-6]) for k in cols])
    times=np.array([float(r['trial_s']) for r in rows]);fig,ax=plt.subplots(figsize=(12,5),layout='constrained')
    release=None;pressure=None
    with (folder/'events.csv').open() as f:events=list(csv.DictReader(f))
    base=float(events[0]['host_s']) if events else 0
    for event in events:
        t=float(event['host_s'])-base
        if event['event'] in ('pressure_started','control_action_started'):pressure=t
        elif event['event']=='released':release=t
    selected=[]
    for target in (549,600,661,802):
        k=int(np.argmin(abs(w-target)));values=np.array([float(r[cols[k]]) for r in rows])
        if len(times)>2:
            gap=np.diff(times);values[np.r_[False,gap>2.5*np.median(gap)]]=np.nan
        ax.plot(times,values,label='%.2f nm'%w[k],lw=.9);selected.append((w[k],values))
    for t,label in [(pressure,'pressure / control'),(release,'release')]:
        if t is not None:ax.axvline(t,c='black',ls='--',alpha=.5,label=label)
    ax.set(xlabel='Trial time (s; host receipt)',ylabel='Decoded signal (a.u.)',title='Original-rate masked tissue spectra — unfiltered, no physiological inference')
    ax.legend();ax.grid(alpha=.2);fig.savefig(folder/'original_rate_spectra.png',dpi=180);plt.close(fig)
    if release is not None and pressure is not None:
        fig,ax=plt.subplots(figsize=(12,5),layout='constrained')
        for wavelength,values in selected:
            mask=(times>=max(0,pressure-5))&(times<pressure)&np.isfinite(values)
            if mask.sum()<10:continue
            baseline=values[mask].mean()
            if baseline<=0:continue
            ax.plot(times-release,100*(values/baseline-1),label='%.2f nm'%wavelength,lw=.9)
        ax.axvline(0,c='black',ls='--',label='manual release marker');ax.axhline(0,c='gray',lw=.7)
        ax.set(xlim=(-5,10),xlabel='Seconds from release (host receipt, manual timing uncertainty)',ylabel='Change from pre-pressure reference (%)',title='Release transition at original rate — no 1 s averaging or smoothing')
        ax.legend();ax.grid(alpha=.2);fig.savefig(folder/'release_original_rate.png',dpi=180);plt.close(fig)



def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('session',type=Path);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--annotations',type=Path);p.add_argument('--points-csv',action='store_true');p.add_argument('--reflectance',action='store_true')
    p.add_argument('--common-points',action='store_true',help='Use only tissue points clean throughout all non-artifact frames');p.add_argument('--review',action='store_true');p.add_argument('--plot',action='store_true');p.add_argument('--min-points',type=int,default=10)
    args=p.parse_args()
    if args.min_points<1:p.error('--min-points must be positive')
    ann=json.loads(args.annotations.read_text()) if args.annotations else None
    out=export(args.session,args.output,ann,args.points_csv,args.reflectance,args.review,args.min_points,args.common_points)
    if args.plot:plot_export(out)
    print('Saved derived files:',out)
if __name__=='__main__':main()
