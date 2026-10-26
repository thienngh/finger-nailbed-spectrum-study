# Mac and Jetson over a phone hotspot

The Jetson owns the camera and runs the SDK, decoder and web server. The Mac stores source code, provides the remote terminal/browser and receives the end-of-day data. The phone supplies a shared network. `localhost` refers to whichever computer a process is running on; the SSH tunnel connects the Mac browser to the Jetson's localhost server.

## 1. Get both devices onto the same phone hotspot

Prepare the downloaded code while you have internet. Power the Jetson and camera. For changing the Jetson's own Wi-Fi hotspot into a Wi-Fi client, use its connected monitor/keyboard/terminal: changing the connection can disconnect SSH. Do not turn off networking, SSH or the whole Jetson to disable its access point.

On iPhone, open **Settings → Personal Hotspot → Allow Others to Join** and keep that page open while joining. On the Mac, select that phone's hotspot in Wi-Fi settings. Use the private password displayed on the phone. If the hotspot is not visible, check Maximize Compatibility on supported iPhones. [Apple's hotspot instructions](https://support.apple.com/en-us/111785).

On the **Jetson console**, inspect its connection and device names:

```bash
nmcli device status
nmcli -f NAME,TYPE,DEVICE connection show --active
nmcli device wifi list
```

If its Wi-Fi interface is hosting `locamera106`, note the **actual access-point connection profile name** shown above. Use that exact name below (replace the text, do not paste the placeholder unchanged):

```bash
nmcli connection down "ACTUAL_JETSON_ACCESS_POINT_PROFILE"
nmcli --ask device wifi connect "YOUR_PHONE_HOTSPOT_NAME"
```

The second command prompts for the phone Wi-Fi password; it is not an SSH password. Keep it out of shell history. If Ubuntu asks for network-administration authorization, authenticate locally as appropriate. With multiple Wi-Fi interfaces, specify the intended `ifname` after inspecting device status. Do not guess interface/profile names or delete the saved access-point profile. These commands follow [NetworkManager's nmcli documentation](https://networkmanager.dev/docs/api/latest/nmcli.html).

## 2. Find the current Jetson address

On the **Jetson console**:

```bash
hostname
hostname -I
ip -4 -brief address
nmcli device status
```

Identify the IPv4 address on the Wi-Fi interface that joined the phone. Ignore loopback (`127.0.0.1`) and Docker bridge addresses. The previous phone-hotspot address was `172.20.10.13`; use the address shown **today**. Changing Wi-Fi mode does not change the Jetson's username: the established account is `admin`, hostname `locamera106`.

On the **Mac**:

```bash
export JETSON_HOST=admin@172.20.10.13
ssh -o ConnectTimeout=10 "$JETSON_HOST"
```

Replace the IP first. Enter the Jetson account password (hidden while typing). Do not put passwords into shared scripts or GitHub. Verify a first-connection host-key fingerprint with the Jetson/operator before accepting it; an unexpected changed-key warning needs investigation rather than blindly removing the key. `admin@locamera106:~$` means commands now run on the Jetson.

## 3. Keep two terminals separate

**Terminal A** — Mac SSH shell, then Jetson recorder:

```bash
ssh admin@172.20.10.13
source /opt/lo/sdk-1.12.1/venv/bin/activate
cd ~/fingertip-spectrum
python fast_spectrum.py --fps 30 --exposure-ms 10 --gain-db 0 \
  --require-references --study-id study_20261027 --output recordings_spatial
```

**Terminal B** — local Mac tunnel:

```bash
ssh -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ExitOnForwardFailure=yes \
  -N -L 8765:127.0.0.1:8765 admin@172.20.10.13
```

A blank Terminal B after entering the password means it is staying open to forward traffic. It is not a shell for typing copy commands. In the **Mac browser**, open `http://127.0.0.1:8765`. Keep both terminals open. For an extra Jetson diagnostic shell, open a third local Mac terminal and SSH again; for a file download, stay at the local Mac prompt.

If you use VS Code Remote SSH, files opened in that remote window live on the Jetson. Keep a separate local VS Code window for the downloaded Mac project. The `scp` deployment and bulk download commands run at the **Mac** prompt, because `/Users/...` and `$HOME/Downloads` there refer to the Mac.

## Troubleshooting

| Message | Meaning / next check |
|---|---|
| SSH timed out | Check Jetson power, both devices on the same hotspot, current IP and local connectivity. The old IP may have changed. |
| SSH password rejected | Re-enter the Jetson login password; a successful Wi-Fi connection does not authenticate SSH. |
| Tunnel `connect failed: Connection refused` | SSH reached the Jetson, but no server is listening at Jetson localhost:8765. Check Terminal A and start/fix the recorder. |
| Local `Address already in use` | An existing tunnel/server uses Mac port 8765. Close the known old tunnel or forward `-L 8766:127.0.0.1:8765` and browse Mac port 8766. |
| Browser disconnected after Wi-Fi switch | Restore the same hotspot connection, check the current IP, reconnect SSH and tunnel. Inspect whether the trial was interrupted. |
| `/Users/...` missing on Jetson | Run the transfer from a local Mac terminal, not from `admin@locamera106`. |
| Camera busy / no frames | Check that the Living Optics dashboard's acquisition and other camera programs are stopped. Do not stop network or SSH services. |

If client-to-client access on the phone hotspot fails, use the Jetson's local console to diagnose it; do not assume the phone's router address is the Jetson. Avoid making a remote-only Wi-Fi mode change without a known local recovery path.
