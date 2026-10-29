# Collection protocol and operator checklist

Purpose: compare wavelength-dependent fingertip or nailbed signals around a controlled pressure/release event, preserving spatial data to distinguish tissue changes from clamp/shadow/geometry artifacts. This is an exploratory study, not a diagnostic test or a nerve-block effectiveness claim.

## 1. Decide the study before people arrive

With Dr. Cobert, select **one tissue site** for matched comparisons: nailbed, finger side, palmar finger pad, back of hand or forearm. The email discussion suggested fingernail or palmar finger surface but did not settle a site. Choose it together; do not silently combine nailbed and finger-side trials. If multiple sites are needed, give each its own planned set of trials.

Agree on the intervention, pressure/force, duration, monitoring, stop criteria, recovery and eligibility. The recorder's cuff choices are labels and manual timers only. A qualified clinician directs all cuff interventions; this guide intentionally does not prescribe cuff pressures. The planned-duration field is a reminder, not an automatic release or safety monitor.

For finger compression, agree on a repeatable force/duration and geometry. A clear plate or clear clamp may allow imaging the compressed tissue, but must be evaluated for reflections, distortion, transmission and shadow. Imaging beside a clamp can capture displaced/pooled blood rather than blanching. For a cuff, the camera views the agreed hand/forearm tissue with the cuff outside its view. Decide whether the first phase uses clamp/plate or cuff, then keep matched repeats consistent.

Plan **two pressure/release trials per arm, four per person**, in two collection rounds. Add resting and sham/optical controls; these are separate conditions, not pressure repeats. Keep an operator log of any deviations and excluded trial IDs. Do not use participant names in the recorder; keep any ID key separately under the study team's data procedure.

## 2. Set up the optical measurement

- Join Mac and Jetson to the same phone hotspot using [CONNECTION.md](CONNECTION.md). Start the recorder and tunnel.
- Fix camera position, focus/zoom and distance; measure distance to the tissue plane in cm and record the measurement convention. Use the same support and pose for both rounds. Record left/right arm explicitly.
- Support the hand/finger comfortably and consistently. Use a matte background and keep the tissue of interest large enough to contain multiple spectral samples. Keep direct reflections and clamp shadows away from the selected tissue where possible.
- Use stable broadband illumination with signal across the measured bands. A halogen source was proposed for this study; control its position and avoid changes in tissue temperature from the lighting. Verify usable signal rather than assuming visible brightness guarantees near-IR signal. Living Optics discusses suitable broadband sources [here](https://customers.livingoptics.com/kb/what-light-source-should-i-use-with-the-camera).
- Establish exposure/gain and verify neither the white target nor tissue saturates. Use a test recording and inspect original signals/metadata, not just the auto-contrasted grayscale preview. The preview does not expose a calibrated saturation meter. The code requests fixed exposure/gain and disables auto exposure where supported. It does not implement a separate white-balance lock; inspect the installed SDK/device configuration if applicable. Keep settings fixed throughout matched collection.
- Check actual achieved rate, receipt gaps, free storage and full-frame archive completeness in a hardware test. Target 30 Hz is a request. Ensure the complete planned study fits on both source and destination storage.

## 3. Capture dark and white references

These are **instrument/illumination references**. They are different from the participant's resting baseline.

1. Select a temporary reference box. Cover the lens completely with an opaque cover. Click **Capture dark** and wait until the page says saved. Do not substitute a black clamp: a black object still reflects light and casts shadows.
2. Remove the cover. Put a diffuse white target at the tissue plane, covering the field where sample points will be used. Choose **White paper (approximate reference)** or **Characterized diffuse standard**, matching the real target. Paper is acceptable for a pilot but does not establish certified absolute reflectance; keep the same clean sheet and orientation.
3. Select a box on the white target, click **Capture white reference**, and keep it fixed until saved. Avoid glare or saturation. Both references include per-point means and temporal variation.
4. Remove the target, restore the supported tissue, draw the tissue ROI, and inspect exclusions. Capturing a new dark invalidates the previous white.

Capture references at session start and recapture when illumination, settings, lens geometry or target plane changes. They need not be repeated simply because the participant changes **if the optical setup is truly unchanged**. Switching arms often changes geometry: assess it and recapture when needed. References are not automatically restored after a process restart; capture both again. Each trial copies its reference files into its own folder.

A clear clamp/plate needs separate optical characterization/sham controls under the same geometry. White and dark capture alone do not remove clear-plastic transmission, specular reflections, lensing, displacement or shadows. If measuring transmission through a plate, plan matched references through that optical path with the clinician/optics lead; do not divide by an arbitrary 'clear-clamp spectrum' and assume artifacts are gone. A black-clamp spectrum is useful for identifying contamination, not for electronic dark subtraction. No clamp-specific control is necessary if no clamp/plate is used; lens-covered dark remains necessary with a cuff.

## 4. Measure and record illumination before every assessment

Follow [LIGHTING.md](LIGHTING.md). Use the Sekonic C-800-U in ambient/continuous mode at the tissue plane. Enter a new lux reading, optional CCT/Delta uv/x/y, meter position, source state and memory ID. Confirm it is fresh and record it before Start. The entry is single-use, bound to this participant/arm/site/round/condition, and expires after 10 minutes. Dark/white references still have their separate purpose. No automatic lux-based correction is applied.

## 4a. Rehearse markers and controls

Record a resting condition for stability/noise and a sham condition for apparatus-in-view effects without compression. Agree on their durations. For sham, click Start, wait for ready, mark **control start** when the object enters the planned geometry without pressure, then mark release when it leaves. Do not label an actual pressure trial as resting.

If a clamp is used, collect its spectrum in an explicit control recording under matched illumination, retain all spatial points, and identify its locations offline. Never include clamp points in the tissue average. Keep control trial IDs and notes so they are not counted as pressure repeats. If switching between black and clear devices, use distinct conditions and preserve separate repeated sets when comparisons are intended.

Manual markers log **button receipt time**, with human timing uncertainty. Practice coordinating the operator with actual pressure and release. The current code does not ingest a hardware trigger or automatically detect release. A visible synchronized event cue can be captured in the full scene if planned, but must not illuminate the tissue or contaminate the ROI.

## 5. Run one trial

1. Select participant ID. Their saved name, age, sex, height, weight and skin-site reports load automatically; a new ID clears those fields. Assess/enter the Monk score and its method/site once before that person’s first trial, then use Start trial or Save participant details to save the profile. Select **left/right arm**, collection round, agreed site and actual condition. Returning participants use the same ID. Enter distance, illumination, surface/apparatus, force/pressure notes and planned duration. For cuff conditions, enter the clinician-approved duration explicitly; there is no cuff-duration default.
2. Reposition the participant and recheck focus, reflections and motion. Draw a **new or explicitly rechecked ROI entirely on the intended visible tissue**. Clear old exclusion boxes and draw new ones where needed. If the intended compressed tissue is hidden, correct the setup before recording. The rectangle does not track movement.
3. Tick the participant/arm/site/ROI confirmation box. Click **Start trial + collect baseline**. Keep tissue and lighting still during the default 10-second average. This saves a baseline automatically; there is no separate baseline-click count.
4. Wait for **ready**. The clinician/operator applies the agreed intervention and clicks **Mark pressure/control start** at the event. For resting trials, do not apply pressure or use pressure/release buttons; record for the agreed duration and click Finish.
5. Keep the tissue visible. If an object/shadow moves, update exclusion boxes or flag the contaminated interval as unusable; full spectra and scene images are still retained. Manual masks do not track the object and cannot identify all shading effects. Do not interpret a changing set of points as a physiological effect without reviewing it offline.
6. At release, click **Mark release**. Keep the finger/hand still for the default 20-second recovery recording. The program finishes automatically. This captures original-rate data through the first 1–3 seconds as well as the longer recovery; the browser updates more slowly than the record.
7. Wait for **archive saved**. Check folder ID, errors and achieved rate. 'Saved' is an integrity result, not a quality judgment. If the trial is invalid, note the reason and keep it. A retake is another folder; never overwrite the failed trial.

For a resting recording, Finish after the agreed duration is normal and the archive can be complete. Finishing a pressure trial early does not count as an automatically completed pressure repeat in the progress list. Do not use Ctrl+C to finish a trial normally; it marks an active trial incomplete. Software buttons do not release a physical cuff or clamp.

## 6. Move to the next participant / return for round 2

Keep the camera program running. Select the next participant ID to restore that person’s saved details, then change trial labels and ROI, confirm, and start again. Returning to a participant reuses their saved profile; the score does not need re-entry. Use the same study ID after restarting to find those profiles. There is no restart per participant, no camera power cycle and no per-person file download.

| Round | Sequence |
|---|---|
| 1 | P01 left, P01 right; P02 left, P02 right; P03 left, P03 right; P04 left, P04 right |
| 2 | P01 left, P01 right; P02 left, P02 right; P03 left, P03 right; P04 left, P04 right |

Use the same agreed condition/site for matched repeats. Record deviations such as order changes. The recorder blocks new pressure trials on an arm until at least **15 minutes after its last logged pressure/release event**, across sites and conditions. The timer survives a restart when the same output/study ID is used. After an intervention without a release marker, it conservatively uses the last logged finish/start time; verify actual timing with the clinician before continuing. Clock changes, forgotten markers, a new study ID or interventions outside the app can invalidate the reminder. The clinician determines recovery and any cross-arm/systemic carryover restrictions; the timer cannot establish readiness.

If people rotate quickly, wait before round 2. Do not shorten recovery solely to keep the recording schedule. Optional skin descriptors should be collected before manipulation and recorded with their assessment method/site. Leave unavailable measurements blank. Do not infer ITA or melanin index from this application. Monk was not developed for palmar skin; agree on a suitable assessment approach rather than scoring the finger pad automatically.

## 7. Close, collect all data, check, shut down

Complete all planned participants/repeats, note exclusions, and wait for every archive to finish. Ctrl+C the now-idle recorder, let it exit, then use the single bulk-download command in the README from the **Mac**. Inspect `study_manifest.csv` for participant/arm/site/condition/round, frame counts and completion. Check copied trial IDs against the operator log. The checksum pass verifies file contents, not adequate signal quality or clinical validity. Keep a second copy, then shut down the Jetson cleanly. Do not delete originals after a single transfer.
