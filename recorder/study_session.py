"""Participant/arm trial organization and persisted intervention timing (Python 3.8)."""
import csv
import json
import math
import unicodedata
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from spatial_recording import json_write, PRESSURE_CONDITIONS

SITES = ('nailbed', 'finger_side', 'finger_pad', 'hand_dorsum', 'forearm', 'other')
PROFILE_FIELDS = ('skin_tone_monk', 'skin_tone_fenty', 'fitzpatrick', 'ita',
                  'melanin_index', 'skin_tone_method', 'skin_tone_assessment_site',
                  'full_name', 'age_years', 'biological_sex', 'biological_sex_other',
                  'height_cm', 'weight_kg', 'site_assessments')


def optional_number(value, label, lower, upper, integer=False):
    if value is None or value=='':
        return None
    if isinstance(value,bool):
        raise ValueError(label+' must be a number')
    try:
        number=float(value)
    except (ValueError,TypeError):
        raise ValueError(label+' must be a number')
    if not math.isfinite(number) or not lower<=number<=upper or (integer and number!=int(number)):
        raise ValueError('%s must be %sbetween %s and %s' % (label,'a whole number ' if integer else '',lower,upper))
    return int(number) if integer else number


def fitz_code(value):
    value=str(value or '').strip().upper()
    roman={'I':'1','II':'2','III':'3','IV':'4','V':'5','VI':'6'}
    value=roman.get(value,value)
    if value and value not in ('1','2','3','4','5','6'):
        raise ValueError('Fitzpatrick entry must be 1–6 (or I–VI), or blank')
    return value


def profile_fields(values):
    if not isinstance(values, dict):
        raise ValueError('Participant details must be an object')
    fields = {}
    numeric={'age_years':(0,130,True),'height_cm':(20,300,False),'weight_kg':(.1,700,False)}
    for key in PROFILE_FIELDS:
        if key=='site_assessments':
            continue
        value = values.get(key)
        if key in numeric:
            fields[key]=optional_number(value,key,*numeric[key]);continue
        if value is not None and not isinstance(value, (str,int,float)):
            raise ValueError('Invalid participant detail: '+key)
        value = '' if value is None else str(value).strip()
        if len(value)>200:
            raise ValueError('Participant detail too long: '+key)
        fields[key] = value
    if fields['skin_tone_monk'] and not re.fullmatch(r'(?:[1-9]|10)', fields['skin_tone_monk']):
        raise ValueError('Monk score must be a whole number from 1 to 10, or blank')
    fields['fitzpatrick']=fitz_code(fields['fitzpatrick'])
    if fields['skin_tone_method'] not in ('', 'self_reported', 'operator_visual', 'other'):
        raise ValueError('Choose how the Monk score was assessed')
    if fields['biological_sex'] not in ('','female','male','intersex','self_described','prefer_not_to_say'):
        raise ValueError('Select the participant-reported biological sex')
    rows=values.get('site_assessments') or []
    if not isinstance(rows,list) or len(rows)>40:
        raise ValueError('Use at most 40 site-specific skin entries')
    fields['site_assessments']=[];seen=set()
    for row in rows:
        if not isinstance(row,dict):raise ValueError('Invalid skin-site entry')
        site=str(row.get('site') or '').strip()
        if not site or len(site)>80:raise ValueError('Each skin entry needs a body site (up to 80 characters)')
        arm=row.get('arm','not_specified')
        if arm not in ('left','right','both','not_specified'):raise ValueError('Invalid skin-entry side')
        monk=str(row.get('monk') or '').strip();fitz=fitz_code(row.get('fitzpatrick'))
        if monk and not re.fullmatch(r'(?:[1-9]|10)',monk):raise ValueError('Site Monk score must be 1–10 or blank')
        if not monk and not fitz:raise ValueError('Enter at least one self-reported score for each site')
        entry_id=token(row.get('id') or uuid.uuid4().hex,'Skin-entry ID')
        if entry_id in seen:raise ValueError('Duplicate skin-entry ID')
        seen.add(entry_id)
        fields['site_assessments'].append(dict(id=entry_id,site=site,arm=arm,monk=monk,
            fitzpatrick=fitz,method='self_reported',interpretation='Site-specific participant report; Fitzpatrick is normally a person-level sun-response phototype'))
    return fields


def selected_skin_entry(fields, entry_id):
    if not entry_id:
        return None
    for row in fields['site_assessments']:
        if row['id']==entry_id:return dict(row)
    raise ValueError('Selected skin-site entry no longer exists; select it again')


def readable_trial_name(fields, skin, stamp):
    name=unicodedata.normalize('NFKD',fields.get('full_name') or 'Participant').encode('ascii','ignore').decode()
    name=re.sub(r'[^A-Za-z0-9]+','_',name).strip('_')[:50] or 'Participant'
    monk=(skin or {}).get('monk') or fields.get('skin_tone_monk') or 'NA'
    fitz=(skin or {}).get('fitzpatrick') or fields.get('fitzpatrick') or 'NA'
    return '%s_Monk%s_Fitz%s_%s' % (name,monk,fitz,stamp)


def token(value, label):
    value = str(value or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', value):
        raise ValueError(label+' must use 1-64 letters, numbers, underscores or hyphens')
    return value


def validate_labels(payload):
    participant = token(payload.get('participant_id'), 'Participant ID').upper()
    arm = payload.get('arm')
    site = payload.get('site')
    if arm not in ('left', 'right'):
        raise ValueError('Select left or right arm')
    if site not in SITES:
        raise ValueError('Select the tissue site agreed with Dr. Cobert')
    if payload.get('roi_confirmed') is not True:
        raise ValueError('Confirm the ROI and labels for this participant and arm')
    round_number = payload.get('round')
    if round_number not in (1, 2):
        raise ValueError('Select collection round 1 or 2; retakes get new trial folders')
    return participant, arm, site, int(round_number)


class Study:
    def __init__(self, root, study_id, retest_seconds=900):
        self.study_id = token(study_id, 'Study ID')
        self.root = Path(root).resolve()/self.study_id
        self.retest_seconds = float(retest_seconds)
        self._cache = None
        self._cache_at = 0

    def participant(self, participant):
        participant = token(participant, 'Participant ID').upper()
        path = self.root/participant/'participant.json'
        if not path.exists():
            return None
        try:
            profile = json.loads(path.read_text())
            if (profile.get('participant_id')!=participant or profile.get('schema_version')!=1
                    or not isinstance(profile.get('revision'), str) or not profile['revision']):
                raise ValueError('Profile identity or version disagrees')
            profile['fields'] = profile_fields(profile['fields'])
            return profile
        except (OSError, KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ValueError('Cannot read saved participant profile: '+str(exc))

    def save_participant(self, participant, values, expected_revision=None, check_revision=True):
        participant = token(participant, 'Participant ID').upper()
        fields = profile_fields(values)
        previous = self.participant(participant)
        revision = previous['revision'] if previous else None
        if check_revision and expected_revision!=revision:
            raise ValueError('Participant profile changed; reload that participant before saving')
        if previous and fields==previous['fields']:
            return previous
        profile = dict(schema_version=1, participant_id=participant, fields=fields,
                       revision=uuid.uuid4().hex,
                       updated_utc=datetime.now(timezone.utc).isoformat())
        path = self.root/participant/'participant.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            json_write(path, profile)
        except OSError as exc:
            raise ValueError('Could not save participant profile: '+str(exc))
        return profile

    def new_folder(self, participant, arm, site, condition, fields=None, skin=None):
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
        name=readable_trial_name(fields,skin,stamp) if fields is not None else stamp
        return self.root/participant/arm/site/condition/name

    def intervention(self, participant, arm, event, trial_folder):
        # One clock per arm, across tissue sites and conditions. A start with no
        # release remains a reminder after a crash; never infer actual cuff state.
        path = self.root/participant/arm/'last_intervention.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        json_write(path, dict(event=event, utc=datetime.now(timezone.utc).isoformat(),
                              epoch_s=time.time(), trial=str(trial_folder.relative_to(self.root))))

    def remaining(self, participant, arm):
        path = self.root/participant/arm/'last_intervention.json'
        if not path.exists():
            return 0
        try:
            saved = json.loads(path.read_text())
            timestamp = float(saved['epoch_s'])
            if timestamp > time.time()+5:
                raise ValueError('System clock moved backwards; verify intervention timing')
            return max(0, self.retest_seconds-(time.time()-timestamp))
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise ValueError('Cannot verify arm rest timer: '+str(exc))

    def check_rest(self, participant, arm, condition):
        if condition in PRESSURE_CONDITIONS:
            remaining = self.remaining(participant, arm)
            if remaining > 0:
                raise ValueError('%s %s: wait %.0f more seconds before repeating pressure; '
                                 'clinician must also confirm recovery' % (participant, arm, remaining))

    def inventory(self, refresh=False):
        if not refresh and self._cache is not None and time.monotonic()-self._cache_at < 2:
            return self._cache
        rows = []
        for path in sorted(self.root.glob('*/*/*/*/*/session.json')):
            row = dict(path=str(path.parent.relative_to(self.root)), complete=False,
                       eligible_repeat=False, reason='missing_or_unreadable_status')
            try:
                config = json.loads(path.read_text())
                labels = config.get('trial_info', {})
                row.update({k: labels.get(k, '') for k in ('participant_id', 'arm', 'site', 'round')})
                row['condition'] = config.get('trial_type', '')
                status_path = path.parent/'recording_status.json'
                status = json.loads(status_path.read_text()) if status_path.exists() else {}
                row.update(complete=bool(status.get('complete')), reason=status.get('reason', 'recording_or_interrupted'))
                summary_path = path.parent/'summary.json'
                summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
                row.update(frames=summary.get('spatial_frames_written', 0), achieved_hz=summary.get('achieved_roi_hz'))
                row['eligible_repeat'] = bool(row['complete'] and row['reason']=='recovery_completed'
                                              and row['condition'] in PRESSURE_CONDITIONS)
            except (OSError, ValueError, TypeError):
                pass
            rows.append(row)
        self._cache = rows
        self._cache_at = time.monotonic()
        return rows

    def snapshot(self):
        rows = self.inventory()
        participants = {r.get('participant_id') for r in rows if r.get('participant_id')}
        participants.update(path.parent.name for path in self.root.glob('*/participant.json'))
        participants = sorted(participants)
        rests = []
        for participant in participants:
            for arm in ('left', 'right'):
                try:
                    remaining = self.remaining(participant, arm)
                    error = None
                except ValueError as exc:
                    remaining, error = None, str(exc)
                rests.append(dict(participant_id=participant, arm=arm, seconds_remaining=remaining, error=error))
        return dict(id=self.study_id, root=str(self.root), trials=rows,
                    participants=participants, rest_timers=rests,
                    retest_seconds=self.retest_seconds)

    def write_inventory(self):
        self.root.mkdir(parents=True, exist_ok=True)
        rows = self.inventory(refresh=True)
        columns = ['participant_id','arm','site','condition','round','complete','eligible_repeat',
                   'reason','frames','achieved_hz','path']
        target = self.root/'study_manifest.csv'
        temp = target.with_suffix('.tmp')
        with temp.open('w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
        temp.replace(target)
        return rows


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Write a study inventory; does not change trial data')
    parser.add_argument('study_path', type=Path)
    args = parser.parse_args()
    if not args.study_path.is_dir():
        parser.error('Study directory does not exist; check the study ID and output path')
    study = Study(args.study_path.resolve().parent, args.study_path.name)
    if not study.inventory(refresh=True):
        parser.error('No trial sessions found; do not treat an empty folder as a completed transfer')
    rows = study.write_inventory()
    print('%d trials; %d complete archives; manifest: %s' %
          (len(rows), sum(r['complete'] for r in rows), study.root/'study_manifest.csv'))
