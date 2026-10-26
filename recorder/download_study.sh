#!/usr/bin/env bash
# Run on the Mac. Copy the whole stopped study, resume partial transfers, verify bytes.
set -euo pipefail
if [ "$#" -ne 3 ]; then
  echo 'Usage: bash download_study.sh admin@JETSON_IP STUDY_ID /absolute/Mac/destination-parent' >&2
  exit 2
fi
remote_host=$1
study_id=$2
destination_parent=$3
case "$remote_host" in ''|*[!a-zA-Z0-9@._-]*|-*) echo 'Invalid SSH host' >&2; exit 2;; esac
case "$study_id" in ''|*[!a-zA-Z0-9_-]*|-*) echo 'Invalid study ID' >&2; exit 2;; esac
case "$destination_parent" in /*) ;; *) echo 'Use an absolute Mac destination path' >&2; exit 2;; esac
remote_root="/home/admin/fingertip-spectrum/recordings_spatial/$study_id"
destination="$destination_parent/$study_id"
mkdir -p "$destination"
echo 'Stop the recorder cleanly before copying. Keep the Jetson powered on and connected.'
ssh -o ConnectTimeout=10 "$remote_host" \
  "test -d '$remote_root' && /opt/lo/sdk-1.12.1/venv/bin/python /home/admin/fingertip-spectrum/study_session.py '$remote_root'"
rsync -av --partial --progress -e 'ssh -o ConnectTimeout=10 -o ServerAliveInterval=15' \
  "$remote_host:$remote_root/" "$destination/"
echo 'Verifying every source file by checksum. This can take a long time.'
check_log=$(mktemp "${TMPDIR:-/tmp}/fingertip-verify.XXXXXX")
trap 'rm -f "$check_log"' EXIT
rsync -rcni --out-format='%i %n%L' -e 'ssh -o ConnectTimeout=10 -o ServerAliveInterval=15' \
  "$remote_host:$remote_root/" "$destination/" > "$check_log"
if [ -s "$check_log" ]; then
  cat "$check_log" >&2
  echo 'Verification found missing/changed files. Run the same command again; keep Jetson originals.' >&2
  exit 1
fi
echo "VERIFIED: all source file contents match in $destination"
echo 'Inspect study_manifest.csv for missing/incomplete trials. Keep Jetson originals as a second copy.'
