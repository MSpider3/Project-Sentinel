#!/bin/bash
set -e

# Project Sentinel - Unified Setup Script
# Handles dependencies, app compilation, service installation, and configuration.

PROJECT_LIB="/usr/lib/project-sentinel"
PROJECT_VAR="/var/lib/project-sentinel"
PROJECT_ETC="/etc/project-sentinel"
SERVICE_FILE="packaging/sentinel-backend.service"
CURRENT_DIR=$(pwd)
LOG_FILE="/tmp/sentinel-setup.log"

# --- Output Helpers ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

MODE=""
VERBOSE=0

info() {
    echo -e "${BLUE}[*]${NC} $1"
}

success() {
    echo -e "${GREEN}[+]${NC} $1"
}

error_msg() {
    echo -e "${RED}[!]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[!]${NC} $1"
}

# --- CLI Parsing ---
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --verbose) VERBOSE=1; shift ;;
        --quiet) VERBOSE=0; shift ;;
        -h|--help)
            echo "Usage: sudo ./setup.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --verbose     Show all raw build output"
            echo "  --quiet       Show only clean progress updates (default)"
            exit 0
            ;;
        *) error_msg "Unknown parameter passed: $1"; exit 1 ;;
    esac
done

if [[ $EUID -ne 0 ]]; then
   error_msg "This script must be run as root (sudo)" 
   exit 1
fi

# Clean previous log
echo "--- Sentinel Setup Log ---" > "$LOG_FILE"

run_cmd() {
    local message="$1"
    shift
    
    info "$message"
    if [ $VERBOSE -eq 1 ]; then
        "$@" | tee -a "$LOG_FILE"
    else
        # Run silently, saving output to log. Show generic working msg.
        "$@" >> "$LOG_FILE" 2>&1 &
        local pid=$!
        local delay=0.1
        local frames=("⠋" "⠙" "⠹" "⠸" "⠼" "⠴" "⠦" "⠧" "⠇" "⠏")
        local num_frames=${#frames[@]}
        local i=0
        while kill -0 $pid 2>/dev/null; do
            printf "\r   \033[1;36m%s\033[0m Working..." "${frames[$i]}"
            i=$(((i+1) % num_frames))
            sleep $delay
        done
        printf "\r\033[K" # Clear the spinner line completely
        
        # Check exit status
        wait $pid
        local status=$?
        if [ $status -ne 0 ]; then
            error_msg "Command failed! Check $LOG_FILE for details."
            exit $status
        fi
    fi
}

echo -e "${BLUE}==========================================${NC}"
echo -e "${BLUE}   Project Sentinel - Setup Wizard${NC}"
echo -e "${BLUE}==========================================${NC}"
if [ $VERBOSE -eq 0 ]; then
    echo "Logs are being saved to: $LOG_FILE"
fi

# 1. Dependency Check
if command -v dnf &> /dev/null; then
    run_cmd "Installing system dependencies..." dnf install -y git gcc meson ninja-build vala gtk4-devel \
                   json-glib-devel gstreamer1-devel gstreamer1-plugins-base-devel \
                   python3-devel pam-devel polkit wget libadwaita-devel
else
    warn "'dnf' not found. Please ensure you have the required dependencies manually:"
    warn "vala, gtk4-devel, libadwaita-devel, json-glib-devel, gstreamer1-devel, python3-devel, pam-devel"
fi
success "System dependencies checked."
echo ""

# 1.5. Model Download
info "Checking AI Models..."
if [ -f "models/download_models.sh" ]; then
    chmod +x models/download_models.sh
    run_cmd "Downloading / Verifying models..." ./models/download_models.sh
else
    warn "'models/download_models.sh' not found. You may need to download models manually."
fi
success "AI models check complete."
echo ""

# 2. Setup TUI Environment
echo ""
info "Preparing TUI Interface..."
if ! command -v uv &> /dev/null; then
    run_cmd "Installing uv package manager..." curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.cargo/bin:$PATH"
fi
success "TUI Environment Ready."

# 3. Full System Install
echo ""
info "Performing Full System Install..."
    
    # Directories
    mkdir -p "$PROJECT_LIB"
    mkdir -p "$PROJECT_VAR"/{models,blacklist}
    mkdir -p "$PROJECT_ETC"
    chmod 700 "$PROJECT_VAR"
    
    # Configure global 'sentinel' command
    cat << 'EOF' > /usr/bin/sentinel
#!/bin/bash
export SENTINEL_SOCKET_PATH=/run/sentinel/sentinel.sock
cd /usr/lib/project-sentinel
uv run sentinel-tui "$@"
EOF
    chmod +x /usr/bin/sentinel
    success "Created global 'sentinel' command."
    
    # Copy Python Code and Packaging
    cp core/*.py "$PROJECT_LIB/" 2>/dev/null || true
    cp -r sentinel-tui "$PROJECT_LIB/"
    cp pyproject.toml "$PROJECT_LIB/"
    cp uv.lock "$PROJECT_LIB/"
    cp Makefile "$PROJECT_LIB/"
    
    # Copy AI Models (daemon reads these from its CWD = /usr/lib/project-sentinel)
    info "Copying AI models..."
    mkdir -p "$PROJECT_LIB/models"
    cp -r models/. "$PROJECT_LIB/models/"
    success "AI models copied."
    
    # Python Venv and Dependencies via uv
    info "Installing Python dependencies natively using uv..."
    cd "$PROJECT_LIB"
    uv sync
    cd "$CURRENT_DIR"
    success "Dependencies locked and loaded."
    
    # Config
    if [ ! -f "$PROJECT_ETC/config.ini" ]; then
        cp backend/config.ini "$PROJECT_ETC/" 2>/dev/null || warn "Creating default config..."
    fi
    
    # Service
    info "Installing systemd Service..."
    cp "$SERVICE_FILE" "/etc/systemd/system/"
    systemctl daemon-reload
    systemctl enable sentinel-backend

    
    # Desktop Application Launcher
    if [ -f "packaging/sentinel-ui.desktop" ]; then
        cp packaging/sentinel-ui.desktop /usr/share/applications/
        update-desktop-database /usr/share/applications/ || true
    fi
    
    # Client Script (for PAM)
    cp backend/sentinel_client.py /usr/bin/sentinel_client.py
    chmod +x /usr/bin/sentinel_client.py
    
    # Fix camera access: ensure the video group exists and allow V4L2 from systemd
    info "Configuring camera device access..."
    # Set video device permissions so the video group can access them
    for dev in /dev/video*; do
        [ -e "$dev" ] && chmod 660 "$dev" && chown root:video "$dev" || true
    done
    # Add executing user to video group so they can also access camera
    REAL_USER=${SUDO_USER:-$(logname 2>/dev/null || echo "")}
    if [ -n "$REAL_USER" ]; then
        usermod -aG video "$REAL_USER" 2>/dev/null && \
            success "Added $REAL_USER to 'video' group (re-login required)" || true
    fi

    # ── SELinux: Fix __pycache__ writes ───────────────────────────────────────
    # Pre-compile all Python files so the daemon never needs to write .pyc at runtime.
    # This eliminates the AVC denial: init_t trying to add_name to lib_t dirs.
    if [ -d "$PROJECT_LIB/venv" ]; then
        info "Pre-compiling Python bytecode (prevents SELinux __pycache__ denial)..."
        "$PROJECT_LIB/venv/bin/python3" -m compileall -q "$PROJECT_LIB/" 2>/dev/null || true
        success "Python bytecode pre-compiled."
    fi

    # ── SELinux: Relabel project lib directory as bin_t ───────────────────────
    # init_t is allowed to execute/read bin_t but not lib_t. Relabeling the
    # project dir allows the daemon to properly import Python modules.
    if command -v chcon &>/dev/null; then
        info "Applying SELinux file context to project lib..."
        chcon -R -t bin_t "$PROJECT_LIB/" 2>/dev/null || true
        success "SELinux context set to bin_t for $PROJECT_LIB/"
    fi

    # ── SELinux: Generate camera access policy module ─────────────────────────
    # Allow the daemon (running as init_t) to open v4l_device_t camera devices.
    if command -v audit2allow &>/dev/null && command -v semodule &>/dev/null; then
        info "Generating SELinux policy module for camera (v4l) access..."
        # Use /run dir — writable by root under SELinux (unlike /tmp which has tmp_t)
        mkdir -p /run/sentinel-setup
        cat > /run/sentinel-setup/sentinel-cam.te << 'SEMODULE_EOF'
module sentinel-cam 1.0;

require {
    type init_t;
    type v4l_device_t;
    class chr_file { read write ioctl open getattr };
    class blk_file { read write ioctl open getattr };
}

# Allow sentinel daemon (init_t) to access V4L2 camera devices
allow init_t v4l_device_t:chr_file { read write ioctl open getattr };
SEMODULE_EOF

        # Compile and install the module
        if checkmodule -M -m -o /run/sentinel-setup/sentinel-cam.mod /run/sentinel-setup/sentinel-cam.te 2>/dev/null && \
           semodule_package -o /run/sentinel-setup/sentinel-cam.pp -m /run/sentinel-setup/sentinel-cam.mod 2>/dev/null && \
           semodule -i /run/sentinel-setup/sentinel-cam.pp 2>/dev/null; then
            success "SELinux camera policy module installed (sentinel-cam)."
        else
            warn "Could not install SELinux policy module automatically."
            warn "If camera fails, run manually:"
            warn "  sudo ausearch -m avc -c sentinel-daemon | audit2allow -M sentinel-cam"
            warn "  sudo semodule -i sentinel-cam.pp"
        fi
        rm -rf /run/sentinel-setup
    fi


    # Secure the log directory (root-only, requires sudo to read)
    mkdir -p /var/log/sentinel
    chmod 750 /var/log/sentinel
    chown root:root /var/log/sentinel

    # Reload and restart with all fixes applied
    cp "$SERVICE_FILE" "/etc/systemd/system/"
    systemctl daemon-reload
    systemctl restart sentinel-backend

    success "Full Installation Complete."


echo ""
echo -e "${YELLOW}==========================================${NC}"
echo -e "${YELLOW}   PAM Configuration (Manual Step)${NC}"
echo -e "${YELLOW}==========================================${NC}"
echo "To enable Face Unlock for Lock Screen / GDM, you must edit PAM config."
echo "I will NOT do this automatically to prevent system lockouts."
echo ""
echo "Edit: /etc/pam.d/gdm-password"
echo "Add this line to the TOP of the 'auth' section:"
echo ""
echo "auth sufficient pam_exec.so expose_authtok quiet /usr/bin/sentinel_client.py"
echo ""
success "Setup Finished!"
