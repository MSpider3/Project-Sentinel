#!/bin/bash
# tools/fix_camera_selinux.sh
# Generates a targeted SELinux policy allowing the sentinel daemon
# to access V4L2 camera devices, without setting SELinux to permissive globally.
#
# Run as: sudo bash tools/fix_camera_selinux.sh

set -e

echo "=== Sentinel Camera SELinux Fix ==="
echo ""

# --- Method 1: Generate policy from AVC denials ---
echo "[1/3] Generating SELinux policy from audit denials..."

if command -v audit2allow &>/dev/null && command -v audit2module &>/dev/null; then
    # Extract recent AVC denials for sentinel-daemon
    ausearch -m AVC,USER_AVC -c sentinel-daemon 2>/dev/null | \
        audit2allow -M sentinel_camera 2>/dev/null && \
        semodule -i sentinel_camera.pp 2>/dev/null && \
        echo "[+] SELinux policy module 'sentinel_camera' installed!" || \
        echo "[-] audit2allow method failed, trying next..."
else
    echo "[-] audit2allow not found. Install: sudo dnf install policycoreutils-python-utils"
fi

# --- Method 2: Direct boolean to allow init_t access to v4l ---
echo "[2/3] Enabling SELinux booleans for device access..."
# Allow all confined services to use video capture devices
setsebool -P allow_domain_fd_use on 2>/dev/null && echo "[+] allow_domain_fd_use=on" || true
getsebool v4l_read_no_label 2>/dev/null && setsebool -P v4l_read_no_label on 2>/dev/null && echo "[+] v4l_read_no_label=on" || true

# --- Method 3: relabel /dev/video* to V4L2 accessible type ---
echo "[3/3] Checking /dev/video* SELinux context..."
for dev in /dev/video*; do
    [ -e "$dev" ] || continue
    current=$(ls -lZ "$dev" 2>/dev/null)
    echo "  $current"
    # Set to v4l_device_t which any domain with video_device access can use
    chcon -t v4l_device_t "$dev" 2>/dev/null && \
        echo "  -> Relabeled $dev to v4l_device_t" || \
        echo "  -> chcon not available or failed"
done

# Restart the daemon to pick up changes
echo ""
echo "Restarting daemon..."
systemctl restart sentinel-backend
sleep 2
echo ""
echo "Daemon SELinux context:"
PID=$(systemctl show sentinel-backend --property=MainPID --value 2>/dev/null)
[ -n "$PID" ] && [ "$PID" != "0" ] && ps -o label= -p $PID 2>/dev/null || echo "Could not get PID"
echo ""
echo "Is daemon active?"
systemctl is-active sentinel-backend
echo ""
echo "=== Done. Try opening the app now and check the log: ==="
echo "    sudo cat /var/log/sentinel/sentinel.log | tail -30"
