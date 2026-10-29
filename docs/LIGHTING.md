# Participant metadata and lighting correction

The recorder saves participant profiles separately from fresh, trial-specific lighting entries. Camera spectra stay unmodified. The equations below describe reference correction and additional measurements needed for stronger interference control; they do not establish a validated physiological measurement.

## Participant profile and naming

Each stable participant ID has a private `P01/participant.json` containing name, age in years, participant-reported biological sex, height in cm, weight in kg and skin reports. Monk and reported Fitzpatrick entries can be recorded for multiple body sites and sides. Select the relevant skin-reference row before the trial. All site rows are explicitly labelled self-reported; do not treat them as instrument measurements. Fitzpatrick is usually an overall sun-response phototype, rather than a local skin-color scale ([dermatologist-authored description](https://dermnetnz.org/topics/skin-phototype)).

An example trial path is:

```text
study_20261029/P01/left/finger_side/clear_clamp/
  Demo_Person_Monk4_Fitz3_20261029T180000_123456Z/
```

The stable P01 parent supports return visits. The readable trial name uses a sanitized name and the selected skin row; unrecorded scores use NA. Corrections to the profile do not rename or modify old trials. Full original name and details remain in the profile and each new trial's immutable profile snapshot. Keep names and study data private; the public repository contains code and synthetic validation only.

## C-800-U entry before every assessment

Use the **C-800-U**, replacing the earlier L-308 plan. The meter measures incident illumination over **380–780 nm**, with continuous-light lux and color readings. Its utility saves measurement data and graph images; Sekonic specifically reserves 1/5 nm numerical spectrum CSV output for the C-7000. Do not assume a C-800-U CSV contains spectral irradiance samples. See [Sekonic specifications](https://sekonic.com/sekonic-c-800-u-spectrometer/) and the [C-800 Utility guide](https://sekonic.com/content/Files/C-800_Utility_Software_Guide_en.pdf).

1. Set up the tissue, camera and lighting as used during recording. Use the meter's ambient/continuous measurement mode, rather than flash exposure mode.
2. Measure at the tissue plane with a repeatable head position/orientation. Avoid shading the measurement with your hand or the meter operator. Record the location/orientation so later trials can reproduce it.
3. Enter illuminance **in lux**. Enter CCT in K, Delta uv, and CIE x/y if available. Leave an unavailable color value blank; CCT need not be meaningful for strongly colored sources.
4. State whether the reading represents all camera illumination, room light only, study source only or another documented configuration. A room-only reading is not the same as the combined light reaching tissue during acquisition.
5. Record the C-800 memory ID and any export/photo filename as a link to the external meter record. The filename field is a reference; it does not upload or parse a file.
6. Confirm that the reading was freshly measured and click Record this lighting measurement. A reading is bound to participant, arm, tissue site, collection round and condition, expires after 10 minutes, and can start only one trial. Start consumes it; the next assessment requires a new reading. Changing setup fields clears the page entry.

The saved `lighting_environment.json` and `session.json` include the entered numbers, meter model/range, binding and receipt timestamp. The timestamp records manual entry, not a synchronized hardware measurement. A single pre-trial meter reading cannot detect lighting changes or flicker during the trial. Unexpected lighting changes require a documented repeat/reference recapture; the app does not automatically identify them.

## Measurement model and main correction

A simplified per-wavelength model is

```text
C(λ,t) = D(λ) + g(λ) × T × E(λ,t) × R(λ,t) × B(t)
         + specular/shadow/motion effects + noise
```

Here C is the decoded camera signal, D the dark offset, g the spectral response, T exposure, E incident spectral illumination, R tissue reflectance and B a geometry factor. Different effects can produce the same recorded change, so there is no single equation that removes all interference from an unreferenced tissue signal.

With matched exposure/gain and a white reference under the same illumination and geometry, use

```text
R_hat(λ,t) = R_white(λ) × [C_tissue(λ,t) − D(λ)] / [C_white(λ) − D(λ)]
```

This is the usual dark/white reflectance correction ([manufacturer explanation](https://resonon.com/blog-raw-radiance-reflectance-hyperspectral-data)). In this recorder, the existing offline exporter performs pointwise dark/white division and assumes unit white reflectance. White paper therefore yields approximate relative reflectance. Weak denominators stay invalid. Camera data are factory decoded arbitrary signals, not independently validated absolute radiance.

The static session white reference helps only while illumination/settings/geometry remain matched. Moving the lamp, changing spectrum, tissue tilt, local shadow and saturation can invalidate that assumption. A C-800 reading can flag differences but does not automatically update the camera white reference.

## Stronger control: simultaneous white target

A stable diffuse reference patch visible beside the tissue in every frame would allow a time-dependent reference:

```text
Q(λ,t) = [C_tissue(λ,t) − D_tissue(λ)] /
         [C_patch(λ,t) − D_patch(λ)]
Delta_OD(λ,t) = −ln[Q(λ,t) / mean_baseline(Q(λ))]
```

This derived double ratio can reduce illumination changes shared by tissue and patch under matched calibrated geometry. It still cannot remove tissue-only shadows, specular reflection, displacement or changes in optical path length. Patch visibility, calibration and location must be reviewed. The current recorder retains all decoded points, including points outside the tissue ROI, which permits future reviewed patch analysis. The current exporter does not yet implement a second dynamic patch ROI; the equations are a proposed next analysis, not an existing live output.

## Ambient-only subtraction requires an additional acquisition

If an active study light can be switched and paired on/off images can be acquired with fixed settings and nearly unchanged tissue, a possible estimator is

```text
R_active(λ,t) ≈ R_white(λ) ×
    [C_tissue,on(λ,t) − C_tissue,off(λ,t)] /
    [C_white,on(λ) − C_white,off(λ)]
```

The off image measures the ambient contribution and subtraction isolates the added light, under stable ambient conditions, linear response and matched timing/geometry. A lens-covered dark image does **not** measure ambient light falling on tissue. The current app does not synchronize light switching or perform this paired acquisition; manual slow switching during changing physiology is not a valid substitute.

## Why dividing by lux is insufficient

Illuminance is a visible-weighted integral:

```text
E_lux = 683 × integral E_spectral(λ) × V(λ) dλ
```

V is the standard photopic sensitivity function ([NIST photometric definition](https://www.nist.gov/pml/sensor-science/optical-radiation/realization-candela)). Because many spectra can have the same weighted integral, lux alone cannot identify each wavelength's illumination. CCT and x/y add useful information but still do not uniquely specify a spectrum. The C-800 range also does not cover the camera's bands above 780 nm.

If spectrum shape and geometry were fixed and only source amplitude changed, scaling by a measured illuminance ratio could be explored after controls. That assumption is not generally justified by equal lux/CCT and is **not** applied automatically here. The program saves the lighting record as a covariate and preserves original spectra.

## Statistical analysis plan

Use dark/white-corrected, artifact-reviewed spectra and within-participant baselines. A candidate model across repeated trials is

```text
Y = participant effect + pressure/release effect
    + beta_lux × log(lux/lux_reference)
    + beta_color × color change + trial/side/site effects + residual
```

Estimate lighting terms using separate resting/sham lighting controls and validate on held-out people. Do not fit nuisance terms to the pressure response itself and then interpret the removed variation as interference. Age, sex, height, weight and skin reports are participant descriptors/covariates, not factors to divide the optical signal by. Four volunteers do not support a large multivariable clinical prediction model. A pre-trial reading helps between-trial comparisons, not frame-by-frame correction of unmeasured intra-trial changes.
