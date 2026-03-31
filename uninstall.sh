#!/bin/bash
set -e

# Project Sentinel - Uninstallation Script
# Removes the application binaries, service, and UI shortcuts.
# Run with --purge to also delete all faces and configuration.

PROJECT_LIB="/usr/lib/project-sentinel"
PROJECT_VAR="/var/lib/project-sentinel"
PROJECT_ETC="/etc/project-sentinel"
SERVICE_NAME="sentinel-backend.service"

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

info() { echo -e "${BLUE}[*]${NC} $1"; }
success() { echo -e "${GREEN}[+]${NC} $1"; }

if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}[!] This script must be run as root (sudo)${NC}" 
   exit 1
fi

echo -e "${BLUE}==========================================${NC}"
echo -e "${BLUE}   Project Sentinel - Uninstall Wizard${NC}"
echo -e "${BLUE}==========================================${NC}"

# 1. Stop and remove systemd service
info "Stopping and disabling systemd service..."
systemctl stop $SERVICE_NAME 2>/dev/null || true
systemctl disable $SERVICE_NAME 2>/dev/null || true

info "Removing systemd service file..."
rm -f "/etc/systemd/system/$SERVICE_NAME"
systemctl daemon-reload

# 2. Remove application files
info "Removing application binaries and virtual environment..."
rm -rf "$PROJECT_LIB"

info "Removing PAM client script..."
rm -f /usr/bin/sentinel_client.py
rm -f /usr/bin/sentinel-ui

info "Removing desktop shortcut..."
rm -f /usr/share/applications/sentinel-ui.desktop
update-desktop-database /usr/share/applications/ 2>/dev/null || true

# 3. Handle data and config
if [[ "$1" == "--purge" ]]; then
    info "Purge requested! Deleting user face data and configurations..."
    rm -rf "$PROJECT_VAR"
    rm -rf "$PROJECT_ETC"
    success "Data and configurations purged."
else
    info "Keeping user face data ($PROJECT_VAR) and config ($PROJECT_ETC)."
    info "Run 'sudo ./uninstall.sh --purge' if you want to completely erase everything."
fi

# 4. Remind about PAM
echo ""
echo -e "${RED}==========================================${NC}"
echo -e "${RED}   ACTION REQUIRED: PAM Configuration${NC}"
echo -e "${RED}==========================================${NC}"
echo "If you manually edited /etc/pam.d/gdm-password during setup,"
echo "you MUST manually remove this line to prevent login issues:"
echo ""
echo "auth sufficient pam_exec.so expose_authtok quiet /usr/bin/sentinel_client.py"
echo ""
echo -e "${RED}==========================================${NC}"

success "Uninstallation complete!"
