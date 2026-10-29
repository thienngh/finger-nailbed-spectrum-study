"""Manual C-800-U lighting metadata; no automatic radiometric correction."""
import uuid
from datetime import datetime, timezone
from study_session import optional_number


def lighting_record(payload, binding):
    if not isinstance(payload,dict) or payload.get('fresh_measurement') is not True:
        raise ValueError('Measure the lighting for this assessment and confirm it is fresh')
    lux=optional_number(payload.get('illuminance_lux'),'C-800-U illuminance (lux)',1,200000)
    if lux is None:raise ValueError('Enter a fresh C-800-U illuminance reading in lux')
    record=dict(id=uuid.uuid4().hex,meter='Sekonic C-800-U',mode='ambient/continuous',
                meter_spectral_range_nm=[380,780],illumination_condition=payload.get('illumination_condition'),
                illuminance_lux=lux,cct_k=optional_number(payload.get('cct_k'),'CCT (K)',1600,40000),
                delta_uv=optional_number(payload.get('delta_uv'),'Delta uv',-1,1),
                chromaticity_x=optional_number(payload.get('chromaticity_x'),'CIE x',0,1),
                chromaticity_y=optional_number(payload.get('chromaticity_y'),'CIE y',0,1),
                fresh_measurement_confirmed=True,entered_utc=datetime.now(timezone.utc).isoformat(),
                timestamp_meaning='Manual entry receipt time; not a synchronized meter acquisition timestamp',
                binding=dict(binding),correction='Recorded covariate only; no automatic lux/CCT spectrum correction')
    if record['illumination_condition'] not in ('all_lights_on','room_only','source_only','other'):
        raise ValueError('Select which lights were on during the meter reading')
    for key in ('meter_position','light_description','meter_memory_id','meter_export_filename'):
        value=payload.get(key) or ''
        if not isinstance(value,str) or len(value)>500:raise ValueError('Invalid lighting field: '+key)
        record[key]=value.strip()
    if not record['meter_position']:
        raise ValueError('Record the meter position and head orientation at the tissue plane')
    x,y=record['chromaticity_x'],record['chromaticity_y']
    if x is not None and y is not None and x+y>1:
        raise ValueError('CIE x + y must not exceed 1')
    return record
