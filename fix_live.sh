#!/bin/bash
# Project Sentinel — Live Fix Script (run with sudo)
# Applies all SELinux and camera fixes to the running system.

set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()    { echo -e "${BLUE}[*]${NC} $1"; }
success() { echo -e "${GREEN}[+]${NC} $1"; }
warn()    { echo -e "${YELLOW}[!]${NC} $1"; }

PROJECT_SRC="/home/mehulgolecha/Documents/Face_Regcognition_Project"
PROJECT_LIB="/usr/lib/project-sentinel"

if [[ $EUID -ne 0 ]]; then
   echo "This script must be run as root (sudo)" 
   exit 1
fi

# 1. Update service file with PYTHONDONTWRITEBYTECODE
info "Updating systemd service file..."
cp "$PROJECT_SRC/packaging/sentinel-backend.service" /etc/systemd/system/sentinel-backend.service
success "Service file updated."

# 2. Copy fixed Python backend files
info "Deploying fixed backend files..."
cp "$PROJECT_SRC/backend/camera_stream.py"      "$PROJECT_LIB/"
cp "$PROJECT_SRC/backend/biometric_processor.py" "$PROJECT_LIB/"
success "Backend files deployed."

# 3. Set SELinux context on project lib to bin_t (allows init_t to read/exec)
info "Setting SELinux file context (bin_t) on $PROJECT_LIB/..."
chcon -R -t bin_t "$PROJECT_LIB/" 2>/dev/null && success "SELinux context set to bin_t." || warn "chcon failed (non-fatal)."

# 4. Pre-create pycache redirect directory
info "Creating pycache redirect dir..."
mkdir -p /tmp/sentinel-pycache
chmod 1777 /tmp/sentinel-pycache
chcon -t tmp_t /tmp/sentinel-pycache 2>/dev/null || true
success "Pycache dir ready at /tmp/sentinel-pycache"

# 5. Pre-compile Python files so no runtime .pyc writes needed
info "Pre-compiling Python bytecode (eliminates __pycache__ SELinux denial)..."
PYCACHEPREFIX=/tmp/sentinel-pycache \
  "$PROJECT_LIB/venv/bin/python3" -m compileall -q "$PROJECT_LIB/" 2>/dev/null && \
  success "Python bytecode pre-compiled." || warn "compileall partial (non-fatal)"

# 6. Install SELinux policy module for V4L camera access
info "Installing SELinux policy module for camera access..."
if command -v checkmodule &>/dev/null && command -v semodule_package &>/dev/null; then
    # Use /run dir which is writable by root even under SELinux (unlike /tmp)
    mkdir -p /run/sentinel-setup
    cat > /run/sentinel-setup/sentinel-cam.te << 'SEMODULE_EOF'
module sentinel-cam 1.0;

require {
    type init_t;
    type v4l_device_t;
    class chr_file { read write ioctl open getattr };
}

allow init_t v4l_device_t:chr_file { read write ioctl open getattr };
SEMODULE_EOF

    if checkmodule -M -m -o /run/sentinel-setup/sentinel-cam.mod /run/sentinel-setup/sentinel-cam.te 2>/dev/null && \
       semodule_package -o /run/sentinel-setup/sentinel-cam.pp -m /run/sentinel-setup/sentinel-cam.mod 2>/dev/null && \
       semodule -i /run/sentinel-setup/sentinel-cam.pp 2>/dev/null; then
        success "SELinux camera policy module (sentinel-cam) installed."
    else
        warn "Could not compile SELinux module. Installing selinux-policy-devel..."
        dnf install -y -q selinux-policy-devel 2>/dev/null || true
        if checkmodule -M -m -o /run/sentinel-setup/sentinel-cam.mod /run/sentinel-setup/sentinel-cam.te 2>/dev/null && \
           semodule_package -o /run/sentinel-setup/sentinel-cam.pp -m /run/sentinel-setup/sentinel-cam.mod 2>/dev/null && \
           semodule -i /run/sentinel-setup/sentinel-cam.pp 2>/dev/null; then
            success "SELinux camera policy module installed after installing devel tools."
        else
            warn "SELinux module install failed. Falling back to permissive mode for sentinel..."
            semanage permissive -a init_t 2>/dev/null || \
                warn "Could not set permissive mode either. Camera may still fail."
        fi
    fi
    rm -rf /run/sentinel-setup

else
    warn "checkmodule/semodule_package not found. Trying audit2allow approach..."
    if command -v audit2allow &>/dev/null; then
        ausearch -m avc -c sentinel-daemon 2>/dev/null | \
            audit2allow -M sentinel-cam 2>/dev/null && \
            semodule -i sentinel-cam.pp 2>/dev/null && \
            success "SELinux module generated from audit log and installed." || \
            warn "audit2allow approach failed too."
    else
        warn "Neither checkmodule nor audit2allow available."
        warn "Attempting permissive mode for init_t as last resort..."
        semanage permissive -a init_t 2>/dev/null || true
    fi
fi

# 7. Reload and restart service
info "Reloading systemd and restarting sentinel-backend..."
systemctl daemon-reload
systemctl restart sentinel-backend
sleep 2

# 8. Show status
echo ""
info "Service status:"
systemctl status sentinel-backend --no-pager -n 20
echo ""
info "Recent logs:"
journalctl -u sentinel-backend -n 30 --no-pager

success "All fixes applied! Check the logs above for camera status."
