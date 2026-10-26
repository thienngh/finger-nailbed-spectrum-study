# Living Optics fingertip / nailbed study recorder

This repository contains the [earlier four-volunteer report](https://thienngh.github.io/finger-nailbed-spectrum-study/) and the updated **full spatial recorder**. The recorder saves every decoded spectral point and scene image during each trial so clamp/shadow contamination can be reviewed later. It organizes repeat visits by participant, left/right arm, tissue site, condition and collection round.

**Start here:** [Mac–Jetson connection](docs/CONNECTION.md) → [collection protocol](docs/PROTOCOL.md) → [files and offline analysis](docs/DATA.md).

Hardware checkout is still required: the new workflow has been tested with synthetic frames on a Mac, not with the camera on a Jetson. This is exploratory optical recording; it does not output validated blood flow, vasodilation, nerve-block success or SpO₂. The earlier report is preliminary and includes the limitations described there.

## What to download

On the **Mac**, download this repository with GitHub's **Code → Download ZIP**, unzip it, and keep the source on the laptop. You need Terminal, a current browser, built-in SSH/rsync, and optionally VS Code. Remote SSH is optional; plain Terminal works. You do **not** need the Mac Living Optics SDK to edit files or view the recorder.

On the **Jetson**, reuse its installed Ubuntu 20.04 / Living Optics SDK 1.12.1 environment at `/opt/lo/sdk-1.12.1/venv`. Do not install the Mac SDK or replace the factory calibration. The acquisition path requires the camera-enabled SDK and NumPy/Pillow already used by the previous program. Offline plots additionally need Matplotlib. The code targets Python 3.8 or newer.

Other equipment: phone hotspot and charger, camera power, monitor/keyboard for Jetson network setup, stable camera and hand supports, ruler for distance, agreed illumination, opaque lens cover, clean diffuse white target, and the clinician-selected pressure apparatus. White paper is an approximate reference; a characterized diffuse standard is preferable. A black clamp is an artifact/control object, **not a dark reference**.

## Quick setup and deployment

First follow [CONNECTION.md](docs/CONNECTION.md). Both Mac and Jetson join the same phone hotspot. Confirm the **current** Jetson Wi-Fi IP; `172.20.10.13` was a previous address, not a permanent one.

**Mac Terminal, local prompt** — edit the IP and source directory below:

```bash
export JETSON_HOST=admin@172.20.10.13
export SOURCE_DIR="$HOME/Downloads/finger-nailbed-spectrum-study-main"
ssh -o ConnectTimeout=10 "$JETSON_HOST" 'mkdir -p /home/admin/fingertip-spectrum'
scp "$SOURCE_DIR"/recorder/{fast_spectrum.py,spatial_recording.py,study_session.py,lo_ratio.py,export_spatial.py,viewer.html} "$JETSON_HOST:/home/admin/fingertip-spectrum/"
ssh "$JETSON_HOST"
```

**Inside SSH, Jetson prompt `admin@locamera106`**:

```bash
source /opt/lo/sdk-1.12.1/venv/bin/activate
cd ~/fingertip-spectrum
python -c "import lo.sdk, numpy, PIL; print('SDK and dependencies ready')"
df -h .
python fast_spectrum.py --fps 30 --exposure-ms 10 --gain-db 0 \
  --require-references --study-id study_20261027 --output recordings_spatial
```

Use a new study ID for a new collection day. These are starting camera settings, not guaranteed adequate illumination/exposure. Establish settings in the hardware checkout, then lock them. Any restart requires recapturing dark/white references. Do not run the dashboard acquisition or another camera program at the same time. If a service owns the camera, identify and stop its acquisition using the established Living Optics dashboard/service procedure; do not guess a service name or stop SSH/networking.

**Second Mac Terminal, local prompt**, leave this running after entering the Jetson password:

```bash
export JETSON_HOST=admin@172.20.10.13
ssh -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ExitOnForwardFailure=yes \
  -N -L 8765:127.0.0.1:8765 "$JETSON_HOST"
```

Open **http://127.0.0.1:8765** in the Mac browser. A blank tunnel terminal is normal. The **Jetson** runs the camera, decoder and web server; the Mac displays the page through SSH.

## Collection order — no camera restart per person

Agree on the tissue site and pressure procedure with Dr. Cobert before starting. There is no default tissue site. Record the same agreed site, illumination and geometry for matched repeats.

| Round | Order |
|---|---|
| 1 | P01 left → P01 right → P02 left → P02 right → P03 left → P03 right → P04 left → P04 right |
| 2 | Return to P01 left/right, then P02 left/right, P03 left/right, P04 left/right |

That is **four pressure trials per person: two left + two right**. Resting, sham and optical controls are additional and do not replace pressure repeats. Returning after only P02 is fine only if the required recovery interval has elapsed and the clinician agrees; rotation alone does not establish recovery. The software enforces a minimum 900-second same-arm repeat interval after a pressure/release marker and shows a timer. It cannot assess physiological recovery or unmarked interventions.

After each trial, wait for **archive saved**, choose the next participant/arm/round/site/condition, recheck the ROI and exclusions, tick the confirmation box, and start. Returning to P01 creates another trial inside P01's existing folder. Use the same participant IDs throughout the day. Each trial gets its own baseline. References can be reused only while the optical setup remains unchanged; see the protocol.

```text
recordings_spatial/study_20261027/
  references/                      # session dark/white captures
  P01/
    left/nailbed/clear_clamp/<UTC trial ID>/
    right/nailbed/clear_clamp/<UTC trial ID>/
  P02/ ...
  study_manifest.csv               # refreshed on shutdown / bulk download
```

The example path does not prescribe nailbed or clamp; actual folders follow your selections. The browser lists previous trials and completed pressure counts by participant/arm/site/condition. Archive completion means files were saved, not that a trial is scientifically usable. Preserve failed trials, document reasons, and record retakes to new folders.

## End of day: transfer the entire study once

1. Finish the final trial and wait for **archive saved**. Note incomplete/invalid trials and their folder IDs.
2. In the Jetson recorder terminal, press **Ctrl+C** once and wait for clean exit and the study-manifest message. A trial interrupted by Ctrl+C is marked incomplete. Do not power off.
3. In a **local Mac Terminal**, run:

```bash
export JETSON_HOST=admin@172.20.10.13
export SOURCE_DIR="$HOME/Downloads/finger-nailbed-spectrum-study-main"
bash "$SOURCE_DIR/recorder/download_study.sh" "$JETSON_HOST" study_20261027 \
  "$HOME/Downloads/Fingertip-Study-Results"
```

This copies **all participant folders plus references** into `Downloads/Fingertip-Study-Results/study_20261027`, resumes partial files after an interruption, then verifies all source-file contents by checksum. It asks for the SSH password as needed; passwords are not stored. Keep both source and copied data. Open `study_manifest.csv`, confirm every planned trial is present, check incomplete statuses, and only then shut down the Jetson cleanly. For a large study, use an approved external SSD destination instead of Downloads.

## Storage and throughput are a required checkout

Full scene images plus all spectra are much larger than the old ROI CSVs. A representative 2048×2432 uint16 scene plus 4,384×96 float32 spectra is about **12 MB per frame, 350 MB/s at 30 Hz, or 21 GB/minute**, before overhead. Actual camera dimensions/dtypes determine the size. Sixteen multi-minute trials can exceed **1 TB**. Check free space on **both** Jetson and Mac/external SSD before people arrive. Test a full planned-duration recording on the Jetson, inspect achieved rate/gaps and `archive_complete`, and verify a transfer. A short low-resolution simulation does not establish hardware throughput.

If disk/decode cannot keep up, the recorder reports an error and marks the trial incomplete; it does not promise 30 Hz or silently decimate the full-frame record. Improve storage/throughput or agree on and document a lower acquisition rate before collecting. Reducing exposure alone does not solve a disk bottleneck.

## Code overview

- `recorder/fast_spectrum.py`: camera acquisition, SDK decoding, references, ROI/masks, trial markers and localhost browser server. Separate acquisition, preview and disk-writing work; preview refresh is slower than recording.
- `recorder/spatial_recording.py`: lossless decoded NPZ frames, bounded writer queue, per-point references, explicit spatial masks and archive status.
- `recorder/study_session.py`: validated labels, participant/arm/site folders, persistent same-arm intervention timer, study inventory.
- `recorder/viewer.html`: preview, label entry, progress, references, trial controls and live spectral graphs.
- `recorder/lo_ratio.py`: adaptation of Living Optics' exploratory 548/599 nm index; not calibrated perfusion. It remains separate from the saved full spectra.
- `recorder/export_spatial.py`: offline review, extra masks, common clean points, per-point CSV and reference-corrected exports.
- `recorder/download_study.sh`: bulk Mac transfer and checksum verification. It never deletes source data.

The earlier recorder saved only ROI averages; those CSVs cannot reconstruct discarded spatial points. This recorder preserves all points before averaging, but decoded NPZ is **not** the SDK's encoded raw format. Read [DATA.md](docs/DATA.md) for file details and analysis limits.

## Optional Mac rehearsal

Use a separate virtual environment; the camera SDK is not required for simulation:

```bash
cd "$HOME/Downloads/finger-nailbed-spectrum-study-main/recorder"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-analysis.txt
python test_spatial_recorder.py
python fast_spectrum.py --simulate --study-id SIM_REHEARSAL --output simulation_recordings
```

Open localhost:8765, rehearse references and labels, and clearly keep synthetic data separate from real studies. The automated tests temporarily disable the repeat timer in simulation only. Live mode retains the 15-minute minimum. Do not upload real participant recordings to this public repository.
