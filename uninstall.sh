#!/bin/bash
set -e

# Project Sentinel - Uninstallation Script (v1.0.0)
# Removes the application binaries, service, and UI shortcuts.
# Safe to run multiple times. Can be run with --purge to also delete user data.
#
# Usage:
#   sudo ./uninstall.sh          # Keep enrolled faces and settings
#   sudo ./uninstall.sh --purge  # Remove everything (faces, config, logs)

PROJECT_LIB="/usr/lib/project-sentinel"
PROJECT_VAR="/var/lib/project-sentinel"
PROJECT_ETC="/etc/project-sentinel"
SERVICE_NAME="sentinel-backend.service"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info() { echo -e "${BLUE}[*]${NC} $1"; }
success() { echo -e "${GREEN}[+]${NC} $1"; }
warning() { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[!]${NC} $1"; }

if [[ $EUID -ne 0 ]]; then
   error "This script must be run as root (sudo)"
   exit 1
fi

echo -e "${BLUE}==========================================${NC}"
echo -e "${BLUE}  Project Sentinel - Uninstall Wizard${NC}"
echo -e "${BLUE}  Version 1.0.0${NC}"
echo -e "${BLUE}==========================================${NC}"
echo ""

# 1. Stop and remove systemd service
info "Stopping daemon service..."
systemctl stop $SERVICE_NAME 2>/dev/null || true
systemctl disable $SERVICE_NAME 2>/dev/null || true

info "Removing systemd service file..."
rm -f "/etc/systemd/system/$SERVICE_NAME"
systemctl daemon-reload 2>/dev/null || true
success "Service removed"

# 2. Remove application binaries and environment
info "Removing application binaries and environment..."
rm -rf "$PROJECT_LIB" 2>/dev/null || true
success "Application files removed"

# 3. Remove user command shortcuts
info "Removing command shortcuts..."
rm -f /usr/bin/sentinel_client.py 2>/dev/null || true
rm -f /usr/bin/sentinel-ui 2>/dev/null || true
rm -f /usr/bin/sentinel 2>/dev/null || true
success "Commands removed"

# 4. Remove desktop integration
info "Removing desktop integration..."
rm -f /usr/share/applications/sentinel-ui.desktop 2>/dev/null || true
rm -f /usr/share/applications/sentinel-greeter.desktop 2>/dev/null || true
update-desktop-database /usr/share/applications/ 2>/dev/null || true
success "Desktop files removed"

# 5. Remove security policies
info "Removing security policies..."
rm -f /etc/polkit-1/rules.d/com.sentinel.rules 2>/dev/null || true
rm -f /usr/share/polkit-1/rules.d/com.sentinel.rules 2>/dev/null || true
semodule -r sentinel-cam 2>/dev/null || true
success "Policies removed"

# 6. Remove udev rules (keep original camera rules)
info "Removing udev rules..."
rm -f /etc/udev/rules.d/83-sentinel-camera.rules 2>/dev/null || true
udevadm control --reload 2>/dev/null || true
success "Udev rules removed"

# 7. Handle daemon logs
info "Removing daemon logs..."
rm -rf /var/log/sentinel 2>/dev/null || true
success "Logs removed"

# 8. Show what's left
echo ""
echo -e "${BLUE}Data Management:${NC}"
if [[ "$1" == "--purge" ]]; then
    info "Purge flag detected - removing all user data..."
    rm -rf "$PROJECT_VAR" 2>/dev/null || true
    rm -rf "$PROJECT_ETC" 2>/dev/null || true
    success "All user data and configuration purged"
else
    if [ -d "$PROJECT_VAR" ]; then
        info "Keeping enrolled faces and user data at: $PROJECT_VAR"
    fi
    if [ -d "$PROJECT_ETC" ]; then
        info "Keeping configuration at: $PROJECT_ETC"
    fi
    warning "Run 'sudo ./uninstall.sh --purge' to completely erase all data"
fi

# 9. PAM configuration check
echo ""
echo -e "${RED}==========================================${NC}"
echo -e "${RED}  ⚠️  IMPORTANT: PAM Configuration${NC}"
echo -e "${RED}==========================================${NC}"
echo ""
warning "If you previously enabled PAM integration during setup,"
echo "  you MUST manually remove the Sentinel line from PAM config:"
echo ""
echo "  Edit: /etc/pam.d/gdm-password"
echo ""
echo "  Remove this line:"
echo "  auth sufficient pam_exec.so expose_authtok quiet /usr/bin/sentinel_client.py"
echo ""
warning "Failure to remove this line will break GDM login!"
echo ""

# 10. Summary
echo -e "${GREEN}==========================================${NC}"
success "Uninstallation complete!"
echo -e "${GREEN}==========================================${NC}"
echo ""
echo "Project Sentinel has been removed from your system."
echo ""
if [[ "$1" == "--purge" ]]; then
    echo "✓ All application files removed"
    echo "✓ All user data removed"
    echo "✓ All configuration removed"
    echo "✓ All logs removed"
else
    echo "✓ Application removed"
    echo "✓ User data preserved (at $PROJECT_VAR)"
    echo "✓ Configuration preserved (at $PROJECT_ETC)"
    echo "✓ You can reinstall without losing your face data"
fi
echo ""
echo "For reinstallation or questions, see DEPLOYMENT_GUIDE.md"
echo ""
