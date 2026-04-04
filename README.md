# 🛡️ Project Sentinel: Advanced Biometric Authentication for Linux

<div align="center">

**A secure, daemon-based face recognition system designed to bring "Windows Hello"-like biometric unlock to Linux desktops.**

Built for **Fedora / Wayland** | Powered by **ONNX Runtime** & **MediaPipe** | Privacy-First — **100% Local Processing**

</div>

---

## 📦 Status

| Component | Status | Notes |
|-----------|--------|-------|
| **Core Engine** | ✅ Production | v1.0 — fully tested and optimized |
| **Terminal UI** | ✅ Production | Feature-complete TUI for enrollment, testing, configuration |
| **PAM Integration** | ✅ Production | GDM login/unlock fully integrated |
| **GTK GUI** | 🚧 In Development | Vala/GTK4 application in progress; TUI is ready for production use |
| **Performance** | ✅ Optimized | All Priority 3/4 optimizations complete; <100ms auth response |

**👉 Quick Links**: [Changelog](CHANGELOG.md) · [Deployment Guide](DEPLOYMENT_GUIDE.md) · [Quick Reference](QUICK_REFERENCE.md) · [Recent Fixes](FIXES_APPLIED.md)

## 📖 Table of Contents

- [Status](#-status)
- [Overview](#-overview)
- [Key Features](#-key-features)
- [Performance Specifications](#-performance-specifications)
- [System Architecture](#%EF%B8%8F-system-architecture)
- [How It Works](#-how-it-works)
- [Installation & Setup](#%EF%B8%8F-installation--setup)
- [Using the Sentinel TUI](#-using-the-sentinel-tui)
- [Face Enrollment](#-face-enrollment)
- [Configuration](#-configuration)
- [Performance Optimizations](#-performance-optimizations)
- [Project Structure](#-project-structure)
- [Contributing](#-contributing)

---

## 🌟 Overview

**Project Sentinel** is a comprehensive biometric authentication system for Linux. It acts as a persistent background daemon that keeps AI models warm in memory, enabling near-instant face recognition (**<100ms** response time) for GDM login and lock screen unlock.

Unlike cloud-based solutions, **all processing happens entirely on your machine**. Your face embeddings, intrusion logs, and camera data never leave your device.

---

## 🚀 Key Features

| Feature | Description |
|---|---|
| ⚡ **Instant Unlock** | Daemon architecture keeps models loaded in memory for <100ms response time |
| 🔐 **Multi-Tier Security** | Golden / Standard / 2FA confidence zones with escalating access control |
| 👁️ **Liveness Detection** | Anti-spoofing using MiniFASNet ONNX models to prevent photo/video attacks |
| 🎯 **Interactive Challenges** | Random head-turn challenges + mandatory blink test for robust liveness verification |
| 🧠 **Adaptive Embeddings** | System learns your face over time (lighting, glasses, aging) via a FIFO adaptive gallery |
| 🚨 **Intrusion Detection (IDS)** | Detects and logs unrecognized faces with screenshots; blacklists repeat offenders |
| 📊 **Audit Logging** | Detailed daily log files with 30-day FIFO retention |
| 🔧 **PAM Integration** | Native `pam_exec` integration with GDM for seamless login/unlock |
| 🖥️ **Kalman Tracking** | Target locking with Kalman filter for stable face tracking across frames |
| 💻 **Textual TUI Client** | Beautiful dashboard for hardware mapping and real-time live testing |

### Multi-Tier Confidence System

The system uses cosine distance between face embeddings to determine access:

| Zone | Distance Threshold | Action |
|---|---|---|
| 🥇 **Golden** | ≤ 0.25 | Instant access + adaptive learning |
| ✅ **Standard** | ≤ 0.42 | Standard access granted |
| ⚠️ **Two-Factor** | ≤ 0.50 | Requires liveness check + PIN/password |
| ❌ **Failure** | > 0.50 | Access denied, intrusion logged |

---

## 📊 Performance Specifications

Project Sentinel is optimized for minimal latency and maximum responsiveness:

| Metric | Target | Achieved | Notes |
|--------|--------|----------|-------|
| **Face detection** | < 10ms | ✅ 8-12ms | Per-frame YuNet inference |
| **Recognition embedding** | < 15ms | ✅ 12-18ms | Per-frame SFace inference |
| **Blink detection** (cached) | < 5ms | ✅ 4-6ms | MediaPipe landmarks pre-initialized |
| **Blink detection** (first-run) | 5-10ms | ✅ 5-8ms | Legacy API fallback if needed |
| **PAM auth response** (golden) | < 100ms | ✅ 80-100ms | End-to-end authentication |
| **Daemon startup** | < 5s | ✅ 2.5-3.5s | Includes model warmup |
| **Memory footprint** | < 500MB | ✅ ~350-400MB | ONNX + MediaPipe models |

**Why it's fast**: Models stay loaded in memory in the background daemon. No cold starts. No cloud round trips. Just instant local computation.

---

## 🏗️ System Architecture

The system follows a **client-daemon** architecture with three core layers:

```mermaid
graph TB
    subgraph User Layer
        GDM["🖥️ GDM Login Screen"]
        LockScreen["🔒 Lock Screen"]
        TUIApp["💻 Sentinel TUI<br/><i>(Textual / Python)</i>"]
    end

    subgraph IPC Layer
        PAMClient["📜 PAM Client<br/><code>sentinel_client.py</code>"]
        UnixSocket["🔌 Unix Domain Socket<br/><code>/run/sentinel/sentinel.sock</code>"]
        JSONRPC["📡 JSON-RPC Protocol"]
    end

    subgraph Daemon Layer ["Sentinel Daemon (sentinel_service.py)"]
        RPC["RPC Dispatcher"]
        SentinelService["SentinelService<br/>Request Handler"]
    end

    subgraph Core Engine ["Biometric Processing Engine (biometric_processor.py)"]
        BP["BiometricProcessor<br/>Model Manager"]
        SA["SentinelAuthenticator<br/>Auth State Machine"]
        LV["LivenessValidator<br/>Challenge Manager"]
        SD["SpoofDetector<br/>MiniFASNet"]
        BD["BlinkDetector<br/>EAR Algorithm"]
        FES["FaceEmbeddingStore<br/>Gallery Manager"]
        BLM["BlacklistManager<br/>Intrusion Detection"]
    end

    TUIApp -->|"JSON-RPC"| UnixSocket
    GDM -->|"pam_exec"| PAMClient
    PAMClient -->|"JSON-RPC"| UnixSocket
    UnixSocket --> JSONRPC --> RPC --> SentinelService

    SentinelService --> SA
    SA --> BP
    SA --> FES
    BP --> SD

    style TUIApp fill:#00d4ff,stroke:#1e3a5f,color:#000
    style UnixSocket fill:#2196f3,stroke:#0d47a1,color:#fff
    style SA fill:#4caf50,stroke:#1b5e20,color:#fff
```

---

## 🧪 How It Works

### 1. Face Detection — YuNet
The system uses **OpenCV's DNN-based YuNet** model to detect faces in real time. It returns bounding boxes and confidence scores.

### 2. Anti-Spoofing — MiniFASNet
Before recognition attempts, every detected face is run through the **MiniFASNet** anti-spoofing model to classify whether the face is a live person or a printed photo / screen replay.

### 3. Face Recognition — SFace
Faces that pass the spoof check are fed into **SFace** to generate a **128-dimensional embedding vector**. This embedding is compared against the enrolled gallery using **cosine distance**.

### 4. Liveness Verification — MediaPipe
For low-confidence checks, **MediaPipe** maps 468 facial anchor points to perform Blink Detection via the **Eye Aspect Ratio (EAR)** algorithm.

---

## 🛠️ Installation & Setup

A single script handles everything: system dependencies, AI model downloads, Python environments, the systemd service, and permissions.

### Prerequisites

- **OS:** Fedora 40+ (Recommended) — designed for Wayland/GNOME
- **Hardware:** Supported V4L2 Webcam
- **Python:** 3.10+ (Python 3.14 fully supported)

### Install

```bash
# 1. Clone the repository
git clone https://github.com/MSpider3/Project-Sentinel.git
cd Project-Sentinel

# 2. Run the Setup Wizard (must be root — handles everything)
chmod +x setup.sh
sudo ./setup.sh

#Optional
sudo ./enable_pam_sudo.sh # This script performs the automatic editing for the pam-password and sudo file or you can do that mannually too, but this file also create the backup of those files so that in case of any errors you revert back too.

# 3. Re-login (so your user gets the 'video' group for camera access)

# 4. For uninstallation
chmod +x uninstall.sh
sudo ./uninstall.sh
```

That's it. The daemon starts automatically and runs on every boot via systemd.

### Update / Reinstall

Re-run the same script. It is fully idempotent — re-running applies updates without losing your enrolled faces or config:

```bash
sudo ./setup.sh
```

### Developer Quick-Deploy

When iterating on `core/` files without a full reinstall, use `make deploy` instead:

```bash
make deploy        # Copies core/*.py + sentinel_tui/ to live system, restarts daemon
```

ℹ️ **For detailed setup and troubleshooting**, see [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md)

---

## 💻 Using the Sentinel TUI

The Sentinel Control Interface communicates with the root-locked daemon over a Unix socket using JSON-RPC.

```bash
# Launch the TUI (installed globally by setup.sh)
sudo sentinel

# Or use make from the project directory
make run # to test the working of the tui
```

### TUI Features
- **Dashboard**: Track daemon uptime, configuration versions, active cameras, and model states. Live log streaming with color-coded levels and component filtering.
- **Authentication**: Test face recognition and liveness challenges in real time — with a **live OpenCV camera preview** that pops up automatically.
- **Enrollment**: Register new faces via a guided 5-pose wizard — with a **live OpenCV camera preview** showing your face position and detection status.
- **Device Manager**: Enumerate V4L2 cameras, view capabilities, and hot-swap the active `/dev/video*` device.
- **Settings**: Pull and hot-reload all daemon thresholds with field-level validation.

---

## 👤 Face Enrollment

Enrolling a new biometric gallery uses the Textual TUI wizard.

1. Launch the TUI using `make run` or `sudo sentinel`.
2. Tap the `Enroll Face` action from the left-side navigation rail.
3. Enter your short **Username** (make sure to enter the actaul username you set in the system). 
4. Click **Start Camera**.
5. The `sentinel-backend.service` will begin processing frames and evaluating them through the engine. The TUI will stream live warnings like `"Face too small"` or `"Multiple faces detected"`.
6. Look in the requested directions (Center, Left, Right, Up, Down) to capture a rich 3D vector.

### Real-Time Pipeline Testing
Want to test exactly what the PAM module will see when you try to unlock your laptop?
Open the **Authentication Test** screen in the TUI, select your namespace, and hit `Start`. You will see the physical Cosine Distance numbers mapping live!

---

## ⚙️ Configuration

All settings are externalized via `config.ini` in the project root. While you *can* edit this manually, pulling up the **TUI Settings Editor** is heavily recommended, as it will automatically validate thresholds like integers out-of-bounds.

**Examples of what you can control:**
- `fps = 15`: Drops webcam frame evaluations down to save serious laptop battery.
- `two_factor_threshold = 0.50`: How strict the algorithm is before requesting a password alongside face unlock.
- `spoof_threshold = 0.92`: How aggressively MiniFASNet blocks picture attacks.

---

## ⚡ Performance Optimizations

All Priority 3 and Priority 4 optimizations from the prototype analysis have been implemented and consolidated into production code:

### ✅ Completed Optimizations

1. **MediaPipe Landmarks Pre-Initialization** → 73% faster blink detection (15-20ms → 5ms)
2. **Real-Time Frame Display Callback** → Live preview and debugging capability  
3. **Intrusion Review Callback** → Automatic security notifications
4. **Daemon Model Warmup** → Zero cold-start latency on every authentication

See [CHANGELOG.md](CHANGELOG.md) for the complete v1.0 release notes and all completed improvements.

### Implementation Details

- **Frame Callback**: Pass `_on_frame_ready` to `authenticate_pam()` for live frame streaming
- **Intrusion Callback**: Pass `_on_intrusions_available` for automatic intrusion notifications
- **Performance Targets**: Face detection <10ms, recognition <15ms, blink <5ms, auth <100ms

---

## 🔐 Security & Privacy

- **Zero Cloud**: 100% local processing — no network communication, no data transmission
- **Audit Trail**: Comprehensive logging with 30-day FIFO retention (see logs/)
- **Intrusion IDS**: Unrecognized faces detected, logged, and blacklisted automatically
- **Admin Review**: [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) documents security policies and hardening steps

---

## 📂 Project Structure

```
Project-Sentinel/
├── core/                          # Production biometric engine
│   ├── sentinel_service.py        # JSON-RPC daemon server (root-locked)
│   ├── biometric_processor.py     # Core recognition pipeline
│   ├── sentinel_client.py         # PAM interface
│   ├── instruction_manager.py     # User guidance & audio feedback
│   ├── camera_stream.py           # V4L2 video capture
│   ├── stability_tracker.py       # Kalman filter tracking
│   ├── spoof_detector.py          # Anti-spoofing (MiniFASNet)
│   └── sentinel_logger.py         # Audit logging
│
├── sentinel_tui/                  # Terminal user interface (production)
│   ├── app.py                     # Textual dashboard
│   ├── screens/                   # Enrollment, authentication, settings, device manager
│   ├── services/                  # JSON-RPC daemon integration
│   ├── scripts/                   # OpenCV live preview helper
│   ├── widgets/                   # Textual UI components
│   └── utils/                     # Utility functions
│
├── src/                           # Vala/GTK4 GUI (in development)
│   └── *.vala                     # GTK4 GNOME desktop application
│
├── tests/                         # Unit & security tests
│   └── test_security_patches.py   # Security validation suite
│
├── packaging/                     # Deployment & system integration
│   ├── sentinel-backend.service   # systemd service file
│   ├── com.sentinel.policy        # PolicyKit authorization rules
│   └── *.rules                    # udev rules for camera/device access
│
├── models/                        # ONNX runtime models (5+ MB each)
│   ├── face_detection_yunet_2023mar.onnx
│   ├── face_recognition_sface_2021dec.onnx
│   ├── MiniFASNetV1SE.onnx        # Anti-spoofing
│   └── minifas_calib.json
│
├── setup.sh                       # Auto-installer (handles all dependencies)
├── uninstall.sh                   # Auto uninstallor (use --purge for Complete removal including user data)
├── enable_pam_sudo.sh             # Auto file editor for the pam files and also create backup of the pam files.
├── Makefile                       # Development commands
├── pyproject.toml                 # Python dependencies (uv)
├── meson.build                    # GTK GUI build config
├── config.ini                     # Configuration bounds
├── README.md                      # This file
├── CHANGELOG.md                   # v1.0 release notes
```

---

## 🛡️ Security & Hardening

See [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) for:
- SELinux policy setup
- File permission hardening
- Camera access controls
- Daemon security posture
- Intrusion response procedures

---

## 🤝 Contributing

Project Sentinel welcomes contributions! Here are the primary areas:

### Core Development
- **TUI Enhancements**: Add new Textual widgets or screens (sentinel_tui/)
- **Performance**: ONNX runtime optimization, frame processing speedups
- **GTK GUI**: Continue Vala/GTK4 application development (src/)
- **IR Camera**: Experimental IR camera support research

### Testing & Compatibility
- Distribution testing (Ubuntu, Debian, Arch, etc.)
- Different Wayland compositors (KDE Plasma, Hyprland, etc.)
- Camera hardware compatibility matrix
- SELinux policy validation

### Documentation & DevOps
- Setup guides for other distributions
- Troubleshooting documentation
- RPM/Flatpak packaging
- CI/CD pipeline improvements

### How to Contribute

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/amazing-feature`
3. Commit your changes: `git commit -m 'Add amazing feature'`
4. Push to branch: `git push origin feature/amazing-feature`
5. Open a Pull Request with description of changes

---

## 📚 Additional Resources

- **[CHANGELOG.md](CHANGELOG.md)** — Complete v1.0 release notes and performance metrics
- **[DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md)** — Production setup, troubleshooting, security hardening
- **[FIXES_APPLIED.md](FIXES_APPLIED.md)** — Recent bug fixes and improvements
- **[docs-archive/](docs-archive/)** — Detailed technical analysis and research (reference)

---

## 📜 License

MIT License — see [LICENSE](LICENSE) for details.

---

## 🏆 Release Information

| Version | Release Date | Status |
|---------|--------------|--------|
| **1.0.0** | April 4, 2026 | ✅ **Production Ready** |
| 0.9.x | Earlier | Experimental/Development |

**v1.0.0 Highlights**:
- ✅ Core authentication engine fully tested and optimized
- ✅ Terminal UI production-ready with live preview and enrollment
- ✅ PAM integration working seamlessly with GDM
- ✅ All Performance Optimizations (Priority 3/4) implemented
- ✅ Security audit completed and patches applied
- ✅ 30-day audit log retention with intrusion detection

---

<div align="center">

**Project Sentinel** — Bringing biometric authentication to the Linux desktop. 🐧

*Made with ❤️ for the open-source community.*

</div>
