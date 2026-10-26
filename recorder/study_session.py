"""Participant/arm trial organization and persisted intervention timing (Python 3.8)."""
import csv
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from spatial_recording import json_write, PRESSURE_CONDITIONS

SITES = ('nailbed', 'finger_side', 'finger_pad', 'hand_dorsum', 'forearm', 'other')


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

    def new_folder(self, participant, arm, site, condition):
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
        return self.root/participant/arm/site/condition/stamp

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
        participants = sorted({r.get('participant_id') for r in rows if r.get('participant_id')})
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
