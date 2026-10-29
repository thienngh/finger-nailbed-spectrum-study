# Saved data, review and reports

## What changed from the earlier published study

The earlier report was built from timestamped ROI-average CSVs and occasional preview images. Once averaged, individual sample spectra cannot be recovered. Old data cannot be retroactively re-masked at full spectral resolution.

The updated recorder keeps **all decoded spectral points and the matching full scene image for every processed trial frame**, plus ROI/masks and timestamps. The point count comes from the SDK calibration; do not hardcode 4,384 or confuse it with a number of wavelength bands. Each point has one value per decoded wavelength (96 in the previous setup). There are two spatial coordinates and a spectrum at each location. This is not a continuous spectrum of every scene-image pixel.

Measurements remain decoded optical signals. Offline dark/white correction provides an approximate relative reflectance `(sample − dark) / (white − dark)` at each point/band; paper is not a characterized absolute standard. Factory calibration, white reference and resting baseline have different purposes. None alone validates a blood-flow measurement. The camera's actual calibrated wavelengths are stored; it cannot supply an unmeasured 940 nm band.

## Per-trial files

| File | Meaning |
|---|---|
| `session.json` | Participant ID, arm, site, round, condition, camera settings, calibration path, actual wavelengths, geometry, ROI, reference context and timing definitions. |
| `frames/frame_XXXXXXXXX.npz` | Original-dtype decoded spectra and full decoded scene; coordinates; masks; wavelength array; frame/time/phase; SDK metadata; operator artifact flag. Lossless NPZ is **not** encoded SDK `.loraw`. |
| `frames.csv` | One row per saved full frame: path, timestamp, phase, total/usable point counts and bytes. |
| `roi_spectra.csv` | Original-rate mean of currently usable ROI points, one column per wavelength in arbitrary units. Missing/invalid tissue is NaN, not fabricated data. |
| `events.csv` | Baseline, pressure/control, release, mask changes, artifact flags and finish markers with host receipt timing. |
| `lighting_environment.json` | Fresh per-trial C-800-U lux/color/context entry; manual receipt timing, not synchronized spectral illumination. |
| `baseline.csv` | Per-band resting baseline mean and temporal SD from this trial. |
| `dark_points.npz`, `white_points.npz` and matching JSON | Per-point reference mean/SD, geometry and context copied into the trial. |
| `lo_ratio.csv`, `lo_ratio_baseline.json` | Optional exploratory vendor-style 548/599 ratio. Normalization differs from physical absorbance and remains scene-sensitive; do not label it calibrated perfusion or SpO₂. |
| `summary.json` | Actual achieved ROI rate, maximum receipt gap, rows/full-frame counts, drops, errors and archive completeness. |
| `recording_status.json` | Incomplete while recording; final completeness status after all writes. |
| `preview_*.jpg` | Sparse display-only previews. These are not the full-rate frame archive. |

Each participant folder has `participant.json` with saved skin-tone descriptors, method/site, update time and revision. Profiles are included in bulk downloads. `session.json` contains an immutable profile snapshot for that trial, so editing the current profile does not rewrite previous trial metadata. Profiles from the earlier version do not exist until entered/verified once.

The study root also has references, persistent per-arm intervention timestamps, and `study_manifest.csv` generated on clean shutdown or before bulk download. A new trial never overwrites a previous one. Returning to a participant appends a new timestamped trial under the same participant/arm/site/condition. Archived geometry is in original image pixels; centered coordinates have ROI center `(0,0)`, x right/y down. They are **not millimeters**, and changing ROI changes the origin. Full original coordinates and point IDs allow later comparison.

Recorded time is **host frame receipt time**, not verified sensor exposure time. Actual exposure/SDK metadata are retained where available. Manual event markers have operator uncertainty; the slower browser preview is not a reliable subsecond release detector. Inspect the saved full-rate scene sequence as well as event timestamps.

## Review before interpreting physiology

Check archive completeness, achieved rate, gaps, saturation, illumination stability, tissue movement, visibility, changing usable-point counts, apparatus intrusion and shadow. A step when a black clamp leaves the frame can be optical contamination. A smooth optical recovery is not established solely by a smoothed ROI curve. Inspect the original-rate data near release without first averaging into one-second bins.

Review masks frame by frame or by intervals. Keep point IDs consistent for a time trace; a moving ROI/exclusion mask can otherwise alter which tissue is averaged. Manual exclusions are not automatic clamp/shadow detection. A common set of clean points across non-artifact frames is available in the exporter; severe movement may leave too few points, which requires a revised recording/analysis window rather than interpolation.

## Offline exports on the Mac

Install the optional analysis requirements in a separate environment as described in the README. Replace the example trial path with an actual directory from the downloaded manifest; choose a **new output directory** for each export.

```bash
cd "$HOME/Downloads/finger-nailbed-spectrum-study-main/recorder"
source .venv/bin/activate
TRIAL="$HOME/Downloads/Fingertip-Study-Results/study_20261027/P01/left/nailbed/clear_clamp/ACTUAL_TRIAL_ID"
python export_spatial.py "$TRIAL" --output "$HOME/Downloads/P01-left-review" --review --plot
```

Open the derived `review.html`, inspect the full-rate sequence, mark clamp/shadow exclusions or whole-frame artifact intervals, and save its annotation JSON. The browser review stores annotations via a downloaded JSON file; it does not edit the source archive. Then export a separate reviewed result:

```bash
python export_spatial.py "$TRIAL" --output "$HOME/Downloads/P01-left-reviewed-export" \
  --annotations "$HOME/Downloads/annotations.json" --common-points \
  --reflectance --points-csv --plot
```

The result includes `frame_spectra.csv` (reviewed original-rate means), optional **`point_spectra.csv.gz`** (every point, coordinates and timestamp, every wavelength), an export summary with point IDs, and original-rate/release-window plots. Gzip CSVs can be very large after decompression; NPZ is the primary lossless archive. Extra review masks are additive; they cannot undo original exclusions. Reference correction leaves unsupported/weak bands invalid rather than clipping or inventing values. Original data remain untouched.

These plots support quality review and exploratory comparisons. The previous cohort report is not automatically regenerated for this new study. For a new clinician report, use reviewed trials, document exclusions and matching site/condition, compare repeatability and sham changes, and treat **participants** as the independent biological units. Thousands of correlated frames or spatial points do not become thousands of independent subjects. Band selection and any ML work require held-out participants/validation; four volunteers do not establish clinical performance or a final device wavelength.

The latest profile/environment fields and reference-correction assumptions are documented in [LIGHTING.md](LIGHTING.md). Trial folder names can contain names and reported scores; keep the complete recordings private.
