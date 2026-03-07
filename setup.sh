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
                   python3-devel pam-devel polkit wget
else
    warn "'dnf' not found. Please ensure you have the required dependencies manually:"
    warn "vala, gtk4-devel, json-glib-devel, gstreamer1-devel, python3-devel, pam-devel"
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

# 2. Compile Vala App
echo ""
run_cmd "Configuring meson build environment..." meson setup builddir --prefix=/usr --wipe
run_cmd "Compiling Vala UI..." ninja -C builddir
success "UI Compilation complete."

# 3. Full System Install
echo ""
info "Performing Full System Install..."
    
    # Directories
    mkdir -p "$PROJECT_LIB"
    mkdir -p "$PROJECT_VAR"/{models,blacklist}
    mkdir -p "$PROJECT_ETC"
    chmod 700 "$PROJECT_VAR"
    
    # Copy Compiled Binary
    cp builddir/sentinel-ui "$PROJECT_LIB/sentinel-val-app"
    chmod +x "$PROJECT_LIB/sentinel-val-app"
    
    # Copy Python Code and Packaging
    cp -r src "$PROJECT_LIB/" 2>/dev/null || true
    cp *.py "$PROJECT_LIB/"
    cp requirements.txt "$PROJECT_LIB/"
    cp pyproject.toml "$PROJECT_LIB/"
    
    # Python Venv
    if [ ! -d "$PROJECT_LIB/venv" ]; then
        run_cmd "Creating Python Virtual Environment..." python3 -m venv "$PROJECT_LIB/venv"
    fi
    
    # Install dependencies gracefully using pyproject.toml
    run_cmd "Installing Python backend packages (this may take a minute)..." "$PROJECT_LIB/venv/bin/pip" install "$PROJECT_LIB"
    
    # Config
    if [ ! -f "$PROJECT_ETC/config.ini" ]; then
        cp config.ini "$PROJECT_ETC/" 2>/dev/null || warn "Creating default config..."
    fi
    
    # Service
    info "Installing systemd Service..."
    cp "$SERVICE_FILE" "/etc/systemd/system/"
    systemctl daemon-reload
    systemctl enable sentinel-backend
    systemctl restart sentinel-backend
    
    # Desktop Application Launcher
    if [ -f "packaging/sentinel-ui.desktop" ]; then
        cp packaging/sentinel-ui.desktop /usr/share/applications/
        update-desktop-database /usr/share/applications/ || true
    fi
    
    # Client Script (for PAM)
    cp sentinel_client.py /usr/bin/sentinel_client.py
    chmod +x /usr/bin/sentinel_client.py
    
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
