#!/bin/bash
# enable_pam_sudo.sh - Safe injection script for Project Sentinel face recognition

if [ "$EUID" -ne 0 ]; then
  echo "Please run this script with sudo."
  exit 1
fi

TARGET="/etc/pam.d/sudo"
BACKUP="/etc/pam.d/sudo.sentinel.bak"
SCRIPT_PATH="/usr/lib/project-sentinel/sentinel_client.py"

echo "Checking Sentinel client script..."
if [ ! -f "$SCRIPT_PATH" ]; then
    echo "ERROR: $SCRIPT_PATH not found. Did you run setup.sh or make deploy?"
    exit 1
fi

# Ensure executable permissions
chmod +x "$SCRIPT_PATH"

# Check if already injected
if grep -q "sentinel_client.py" "$TARGET"; then
    echo "Sentinel is already configured in $TARGET."
    exit 0
fi

echo "Creating backup of $TARGET at $BACKUP..."
cp "$TARGET" "$BACKUP"

echo "Injecting face recognition module into $TARGET..."

# We need to insert our rule exactly below the first line (usually #%PAM-1.0)
PAM_LINE="auth       sufficient   pam_exec.so quiet stdout $SCRIPT_PATH"

# Use awk to insert the line safely right after the first line
awk -v rule="$PAM_LINE" 'NR==1{print; print rule; next}1' "$BACKUP" > /tmp/sudo.new

# Move to target
mv /tmp/sudo.new "$TARGET"
chmod 644 "$TARGET"

echo "✅ Successfully configured sudo to use Sentinel Face Recognition!"
echo "   Run 'sudo ls' in a NEW terminal window to test."
echo ""
echo "   If it breaks, you can restore via:"
echo "   sudo mv /etc/pam.d/sudo.sentinel.bak /etc/pam.d/sudo"
