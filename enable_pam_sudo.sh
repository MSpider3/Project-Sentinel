#!/bin/bash
# enable_pam_sudo.sh - Safe injection script for Project Sentinel face recognition

if [ "$EUID" -ne 0 ]; then
  echo "Please run this script with sudo."
  exit 1
fi

TARGET="/etc/pam.d/sudo"
BACKUP="/etc/pam.d/sudo.sentinel.bak"
PAM_SOURCE="/usr/lib/project-sentinel/archive/pam_sentinel.c"

# If running from source directory, adjust path
if [ -f "archive/pam_sentinel.c" ]; then
    PAM_SOURCE="archive/pam_sentinel.c"
fi

echo "Compiling native Sentinel PAM module..."
if [ ! -f "$PAM_SOURCE" ]; then
    echo "ERROR: $PAM_SOURCE not found. Ensure the archive directory exists."
    exit 1
fi

# Detect PAM directory (Fedora uses /lib64, Ubuntu uses /lib/x86_64-linux-gnu or /lib)
if [ -d "/lib64/security" ]; then
    PAM_DIR="/lib64/security"
elif [ -d "/lib/x86_64-linux-gnu/security" ]; then
    PAM_DIR="/lib/x86_64-linux-gnu/security"
else
    PAM_DIR="/lib/security"
fi

echo "Targeting PAM directory: $PAM_DIR"
if ! gcc -fPIC -shared "$PAM_SOURCE" -o /tmp/pam_sentinel.so -lpam; then
    echo "ERROR: Failed to compile PAM module. Do you have gcc and pam-devel installed?"
    exit 1
fi

cp /tmp/pam_sentinel.so "$PAM_DIR/"
chmod 755 "$PAM_DIR/pam_sentinel.so"
rm /tmp/pam_sentinel.so
echo "Installed pam_sentinel.so to $PAM_DIR"

# Check if already injected
if grep -q "pam_sentinel.so" "$TARGET"; then
    echo "Sentinel is already configured in $TARGET."
    exit 0
fi

echo "Creating backup of $TARGET at $BACKUP..."
cp "$TARGET" "$BACKUP"

echo "Injecting face recognition module into $TARGET..."

# We need to insert our rule exactly below the first line (usually #%PAM-1.0)
PAM_LINE="auth       sufficient   pam_sentinel.so"

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
