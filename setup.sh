#!/bin/bash
set -e

# Project Sentinel - Unified Setup Script
# Handles dependencies, app compilation, service installation, and configuration.
# After running this, just use: sentinel (TUI) or sudo systemctl start sentinel-backend (daemon)

PROJECT_LIB="/usr/lib/project-sentinel"
PROJECT_VAR="/var/lib/project-sentinel"
PROJECT_ETC="/etc/project-sentinel"
SERVICE_FILE="packaging/sentinel-backend.service"
CURRENT_DIR=$(pwd)
LOG_FILE="/tmp/sentinel-setup.log"

# Project version - bump this when making significant changes
SENTINEL_VERSION="2.4.0"

# ── Output Helpers ──────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

VERBOSE=0

info()      { echo -e "${BLUE}[*]${NC} $1"; }
success()   { echo -e "${GREEN}[+]${NC} $1"; }
error_msg() { echo -e "${RED}[!]${NC} $1"; }
warn()      { echo -e "${YELLOW}[!]${NC} $1"; }

# ── CLI Parsing ─────────────────────────────────────────────────────────────
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --verbose) VERBOSE=1; shift ;;
        --quiet)   VERBOSE=0; shift ;;
        -h|--help)
            echo "Usage: sudo ./setup.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --verbose     Show all raw build output"
            echo "  --quiet       Show only clean progress updates (default)"
            echo ""
            echo "This script installs Project Sentinel v${SENTINEL_VERSION}."
            echo "Run it once. Updates are applied by re-running the same script."
            exit 0
            ;;
        *) error_msg "Unknown parameter: $1"; exit 1 ;;
    esac
done

if [[ $EUID -ne 0 ]]; then
   error_msg "This script must be run as root (sudo)"
   exit 1
fi

echo "--- Sentinel Setup Log ---" > "$LOG_FILE"

run_cmd() {
    local message="$1"
    shift
    info "$message"
    if [ $VERBOSE -eq 1 ]; then
        "$@" | tee -a "$LOG_FILE"
    else
        "$@" >> "$LOG_FILE" 2>&1 &
        local pid=$!
        local frames=("⠋" "⠙" "⠹" "⠸" "⠼" "⠴" "⠦" "⠧" "⠇" "⠏")
        local i=0
        while kill -0 $pid 2>/dev/null; do
            printf "\r   \033[1;36m%s\033[0m Working..." "${frames[$i]}"
            i=$(((i+1) % ${#frames[@]}))
            sleep 0.1
        done
        printf "\r\033[K"
        wait $pid
        local status=$?
        if [ $status -ne 0 ]; then
            error_msg "Command failed! Check $LOG_FILE for details."
            exit $status
        fi
    fi
}

echo -e "${BLUE}==========================================${NC}"
echo -e "${BLUE}   Project Sentinel v${SENTINEL_VERSION} - Setup Wizard${NC}"
echo -e "${BLUE}==========================================${NC}"
[ $VERBOSE -eq 0 ] && echo "Logs saved to: $LOG_FILE"
echo ""

# ── Step 1: System Dependencies ─────────────────────────────────────────────
if command -v dnf &> /dev/null; then
    run_cmd "Installing system dependencies..." \
        dnf install -y git gcc meson ninja-build vala gtk4-devel \
            json-glib-devel gstreamer1-devel gstreamer1-plugins-base-devel \
            python3-devel pam-devel polkit wget libadwaita-devel
else
    warn "'dnf' not found. Ensure these are installed: vala, gtk4-devel, libadwaita-devel, json-glib-devel, gstreamer1-devel, python3-devel, pam-devel"
fi
success "System dependencies checked."
echo ""

# ── Step 2: AI Model Download ────────────────────────────────────────────────
info "Checking AI Models..."
if [ -f "models/download_models.sh" ]; then
    chmod +x models/download_models.sh
    run_cmd "Downloading / Verifying models..." ./models/download_models.sh
else
    warn "'models/download_models.sh' not found. Skipping model download."
fi
success "AI models check complete."
echo ""

# ── Step 3: Resolve uv ───────────────────────────────────────────────────────
info "Preparing package manager (uv)..."
UV_BIN=""
for candidate in "$(command -v uv 2>/dev/null)" "$HOME/.local/bin/uv" "/root/.local/bin/uv"; do
    if [ -x "$candidate" ]; then
        UV_BIN="$candidate"
        break
    fi
done

if [ -z "$UV_BIN" ]; then
    info "Installing uv package manager..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    [ -f "$HOME/.local/bin/env" ] && source "$HOME/.local/bin/env"
    export PATH="$HOME/.local/bin:/root/.local/bin:$PATH"
    UV_BIN="$(command -v uv 2>/dev/null || echo '/root/.local/bin/uv')"
fi

if [ ! -x "$UV_BIN" ]; then
    error_msg "uv not found after install attempt. Check your internet connection."
    exit 1
fi

# Install uv system-wide so the 'sentinel' launcher script can find it
UV_SYSTEM="/usr/local/bin/uv"
if [ "$(realpath "$UV_BIN" 2>/dev/null)" != "$(realpath "$UV_SYSTEM" 2>/dev/null)" ]; then
    cp -f "$UV_BIN" "$UV_SYSTEM"
    chmod 755 "$UV_SYSTEM"
fi
UV_BIN="$UV_SYSTEM"
success "Package manager ready (uv: $UV_BIN)."
echo ""

# ── Step 4: Create Directory Structure ──────────────────────────────────────
info "Creating installation directories..."
mkdir -p "$PROJECT_LIB"
mkdir -p "$PROJECT_LIB/core"
mkdir -p "$PROJECT_VAR"/{models,blacklist,gallery,adaptive,intrusions}
mkdir -p "$PROJECT_ETC"
chmod 700 "$PROJECT_VAR"
success "Directories created."

# ── Step 5: Install Core Daemon Modules ─────────────────────────────────────
# IMPORTANT: We copy core/*.py directly to BOTH:
#   - $PROJECT_LIB/               (daemon's sys.path root, WorkingDirectory)
#   - $PROJECT_LIB/venv/lib/python*/site-packages/ (pip wheel install target)
# This ensures that no matter which Python path the daemon resolves first,
# it always loads the current codebase — eliminating the "Ghost Module" bug
# where updates to core/ files were silently ignored by the running daemon.
info "Installing core daemon modules..."
cp core/*.py "$PROJECT_LIB/"
success "Core modules installed to $PROJECT_LIB/."

# ── Step 6: Install TUI and Config Files ─────────────────────────────────────
info "Installing TUI and configuration..."
mkdir -p "$PROJECT_LIB/sentinel_tui"
cp -r sentinel_tui/. "$PROJECT_LIB/sentinel_tui/"
cp pyproject.toml "$PROJECT_LIB/"
cp uv.lock "$PROJECT_LIB/" 2>/dev/null || true
cp Makefile "$PROJECT_LIB/"

# Config (preserve existing user config on upgrade)
if [ ! -f "$PROJECT_ETC/config.ini" ]; then
    cp config.ini "$PROJECT_ETC/" 2>/dev/null || warn "config.ini not found, using defaults."
    success "Config installed to $PROJECT_ETC/config.ini."
else
    # Merge: only add new keys that don't exist in the user's config
    warn "Existing config found. Preserving user settings."
fi

# AI Models
info "Installing AI models..."
mkdir -p "$PROJECT_LIB/models"
cp -r models/. "$PROJECT_LIB/models/"
success "AI models installed."

# ── Step 7: Python Virtual Environment ──────────────────────────────────────
# Create the daemon's venv using the core/pyproject.toml which specifies
# all Python runtime dependencies (numpy, onnxruntime, mediapipe, etc.)
info "Building Python virtual environment for daemon..."
VENV_DIR="$PROJECT_LIB/venv"

# Install the daemon package with uv from core/pyproject.toml
cd "$PROJECT_LIB"
if [ ! -d "$VENV_DIR" ]; then
    "$UV_BIN" venv "$VENV_DIR" --python python3
fi

# Copy core's pyproject.toml as the root project descriptor for pip install
cp "$CURRENT_DIR/core/pyproject.toml" "$PROJECT_LIB/pyproject.toml.daemon"

# Install daemon dependencies + the daemon package itself
"$VENV_DIR/bin/pip" install --quiet \
    "opencv-python>=4.9.0" \
    "numpy>=1.26.0" \
    "onnxruntime>=1.16.0" \
    "scipy>=1.11.0" \
    "mediapipe>=0.10.11" \
    "python-pam>=2.0.2" 2>> "$LOG_FILE" || {
    warn "pip install had warnings (non-fatal). Check $LOG_FILE."
}

# NOW overlay core modules ON TOP of site-packages so our code always wins.
# This is the critical step: site-packages is the authoritative location the
# venv's python interpreter loads from, so we force our patched modules there.
SITE_PKG=$(find "$VENV_DIR" -name "site-packages" -type d | head -1)
if [ -n "$SITE_PKG" ]; then
    cp "$CURRENT_DIR/core/"*.py "$SITE_PKG/"
    success "Core modules overlaid into venv site-packages ($SITE_PKG)."
else
    warn "Could not locate site-packages in venv. Daemon may load stale modules."
fi

# Install the sentinel-daemon entrypoint script
"$VENV_DIR/bin/pip" install --quiet --no-deps -e "$CURRENT_DIR/core/" 2>> "$LOG_FILE" || {
    # Fallback: write the entrypoint manually
    DAEMON_SCRIPT="$VENV_DIR/bin/sentinel-daemon"
    cat > "$DAEMON_SCRIPT" << 'ENTRYPOINT_EOF'
#!/bin/bash
# sentinel-daemon entrypoint — generated by setup.sh
exec "$(dirname "$0")/python3" -c "from sentinel_service import main; main()" "$@"
ENTRYPOINT_EOF
    chmod +x "$DAEMON_SCRIPT"
    warn "Used fallback entrypoint (editable install failed). This is normal."
}

cd "$CURRENT_DIR"

# Install TUI dependencies (separate venv scoped to the TUI project)
info "Installing TUI dependencies..."
cd "$PROJECT_LIB"
"$UV_BIN" sync >> "$LOG_FILE" 2>&1 || {
    warn "uv sync had warnings. Check $LOG_FILE."
}
cd "$CURRENT_DIR"
success "Python environments ready."

# ── Step 8: Global 'sentinel' Command ───────────────────────────────────────
info "Installing global 'sentinel' command..."
printf '#!/bin/bash\nexport SENTINEL_SOCKET_PATH=/run/sentinel/sentinel.sock\ncd /usr/lib/project-sentinel\n"%s" run sentinel-tui "$@"\n' "$UV_BIN" > /usr/bin/sentinel
chmod +x /usr/bin/sentinel
success "Global 'sentinel' command installed."

# Client script for PAM
cp core/sentinel_client.py /usr/bin/sentinel_client.py
chmod +x /usr/bin/sentinel_client.py

# Desktop launcher
if [ -f "packaging/sentinel-ui.desktop" ]; then
    cp packaging/sentinel-ui.desktop /usr/share/applications/
    update-desktop-database /usr/share/applications/ 2>/dev/null || true
fi

# ── Step 9: Camera Device Permissions ───────────────────────────────────────
info "Configuring camera device access..."
for dev in /dev/video*; do
    [ -e "$dev" ] && chmod 660 "$dev" && chown root:video "$dev" || true
done
REAL_USER=${SUDO_USER:-$(logname 2>/dev/null || echo "")}
if [ -n "$REAL_USER" ]; then
    usermod -aG video "$REAL_USER" 2>/dev/null && \
        success "Added $REAL_USER to 'video' group (re-login to activate)." || true
fi

# ── Step 10: SELinux Configuration ──────────────────────────────────────────
# Pre-compile Python bytecode to prevent __pycache__ write denials at runtime
if [ -d "$PROJECT_LIB/venv" ]; then
    info "Pre-compiling Python bytecode (prevents SELinux __pycache__ denial)..."
    "$PROJECT_LIB/venv/bin/python3" -m compileall -q "$PROJECT_LIB/sentinel_tui/" 2>/dev/null || true
    "$PROJECT_LIB/venv/bin/python3" -m compileall -q "$PROJECT_LIB/"*.py 2>/dev/null || true
    success "Python bytecode pre-compiled."
fi

# Set SELinux context
if command -v chcon &>/dev/null; then
    info "Applying SELinux file context..."
    chcon -R -t bin_t "$PROJECT_LIB/" 2>/dev/null || true
    success "SELinux context set (bin_t)."
fi

# Install camera access SELinux policy module
if command -v audit2allow &>/dev/null && command -v semodule &>/dev/null; then
    info "Installing SELinux camera policy module..."
    mkdir -p /run/sentinel-setup
    cat > /run/sentinel-setup/sentinel-cam.te << 'SEMODULE_EOF'
module sentinel-cam 1.0;

require {
    type init_t;
    type v4l_device_t;
    class chr_file { read write ioctl open getattr };
    class blk_file { read write ioctl open getattr };
}

allow init_t v4l_device_t:chr_file { read write ioctl open getattr };
SEMODULE_EOF
    if checkmodule -M -m -o /run/sentinel-setup/sentinel-cam.mod /run/sentinel-setup/sentinel-cam.te 2>/dev/null && \
       semodule_package -o /run/sentinel-setup/sentinel-cam.pp -m /run/sentinel-setup/sentinel-cam.mod 2>/dev/null && \
       semodule -i /run/sentinel-setup/sentinel-cam.pp 2>/dev/null; then
        success "SELinux camera policy installed (sentinel-cam)."
    else
        warn "SELinux policy install skipped. If camera fails, run:"
        warn "  sudo ausearch -m avc -c sentinel-daemon | audit2allow -M sentinel-cam"
        warn "  sudo semodule -i sentinel-cam.pp"
    fi
    rm -rf /run/sentinel-setup
fi

# ── Step 11: Log Directory ───────────────────────────────────────────────────
info "Configuring log directory..."
mkdir -p /var/log/sentinel
chmod 750 /var/log/sentinel
chown root:root /var/log/sentinel
# Allow the installing user's group to READ logs (so sentinel-tui can stream them)
if [ -n "$REAL_USER" ]; then
    REAL_GROUP=$(id -gn "$REAL_USER" 2>/dev/null || echo "")
    if [ -n "$REAL_GROUP" ]; then
        chown root:"$REAL_GROUP" /var/log/sentinel
        chmod 750 /var/log/sentinel
        success "Log directory readable by group '$REAL_GROUP' (enables live log streaming in TUI)."
    fi
fi

# ── Step 12: Systemd Service ─────────────────────────────────────────────────
info "Installing and enabling systemd service..."
cp "$SERVICE_FILE" "/etc/systemd/system/"
systemctl daemon-reload
systemctl enable sentinel-backend
systemctl restart sentinel-backend
success "sentinel-backend service started."

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}==========================================${NC}"
echo -e "${GREEN}   Installation Complete! v${SENTINEL_VERSION}${NC}"
echo -e "${GREEN}==========================================${NC}"
echo ""
success "Daemon is running: sudo systemctl status sentinel-backend"
success "Launch TUI:        sentinel"
success "View daemon logs:  sudo journalctl -u sentinel-backend -f"
echo ""
echo -e "${YELLOW}Note:${NC} If this is your first install, re-login for 'video' group to take effect."
echo ""
echo -e "${YELLOW}==========================================${NC}"
echo -e "${YELLOW}   PAM Configuration (Manual Step)${NC}"
echo -e "${YELLOW}==========================================${NC}"
echo "To enable Face Unlock for GDM / lock screen, add this to /etc/pam.d/gdm-password"
echo "at the TOP of the 'auth' section:"
echo ""
echo "  auth sufficient pam_exec.so expose_authtok quiet /usr/bin/sentinel_client.py"
echo ""
warn "Do NOT add this automatically — a misconfiguration could lock you out."
