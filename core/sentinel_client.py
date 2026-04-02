#!/usr/bin/env python3
"""
sentinel_client.py - Lightweight PAM Client
Connects to the Sentinel Daemon to authenticate the current user.
Exits with 0 (Success) or 1 (Failure).
"""
import socket
import sys
import json
import os
import signal
import pwd
import glob

SOCKET_PATH = os.environ.get('SENTINEL_SOCKET_PATH', "/run/sentinel/sentinel.sock")

def main():
    # PAM passes the username in PAM_USER var (sometimes) or we get it from env
    user = os.environ.get('PAM_USER') or os.environ.get('USER')
    
    if not user:
        # Fallback for testing
        if len(sys.argv) > 1:
            user = sys.argv[1]
        else:
            print("Error: No user specified")
            sys.exit(1)

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(5.0) # 5 Second Max Timeout
    
    try:
        sock.connect(SOCKET_PATH)
        
        # Capture GUI context for preview window
        display = os.environ.get('DISPLAY')
        wayland_display = os.environ.get('WAYLAND_DISPLAY')
        xdg_runtime = os.environ.get('XDG_RUNTIME_DIR')
        xauth = os.environ.get('XAUTHORITY')
        
        # If running via sudo/pam_exec, env is likely stripped. 
        # Attempt to find the user's active graphical session.
        if not display and not wayland_display:
            display = ":0" # Most common local display fallback
            
        if not xauth and user:
            # 1. Try common locations for .Xauthority based on username
            try:
                user_info = pwd.getpwnam(user)
                home_dir = user_info.pw_dir
                uid = user_info.pw_uid
                
                if not xdg_runtime:
                     xdg_runtime = f"/run/user/{uid}"

                # Check ~/.Xauthority
                potential_xauth = os.path.join(home_dir, ".Xauthority")
                if os.path.exists(potential_xauth):
                    xauth = potential_xauth
                
                # 2. Check Fedora-style GNOME path (/run/user/1000/xauth_...)
                else:
                    if os.path.exists(xdg_runtime):
                        matches = glob.glob(os.path.join(xdg_runtime, "xauth_*"))
                        if matches:
                            xauth = matches[0]
            except Exception:
                pass

        gui_context = {
            "display": display,
            "wayland_display": wayland_display,
            "xdg_runtime_dir": xdg_runtime,
            "xauthority": xauth
        }
        
        # Request validation for ANY enrolled user by sending None if we want global auth
        # For a personal laptop, letting any enrolled face unlock sudo is preferred.
        req = {
            "jsonrpc": "2.0",
            "method": "authenticate_pam",
            "params": {
                "user": None,
                "gui_context": gui_context
            },
            "id": 100
        }
        
        sock.sendall((json.dumps(req) + "\n").encode('utf-8'))
        
        # Response - Read line
        # We read chunk by chunk looking for newline
        data = b""
        while b"\n" not in data:
            chunk = sock.recv(1024)
            if not chunk: break
            data += chunk
            
        line = data.decode('utf-8').strip()
        if not line:
            sys.exit(1)
            
        resp = json.loads(line)
        result = resp.get('result', {})
        
        status = result.get('result', 'FAILED')
        
        if status == 'SUCCESS':
            sys.exit(0) # Logic True
        else:
            sys.exit(1) # Logic False

    except Exception:
        sys.exit(1) # Fail safe
    finally:
        sock.close()

if __name__ == "__main__":
    main()
