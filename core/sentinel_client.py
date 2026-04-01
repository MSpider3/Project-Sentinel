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
        xauth = os.environ.get('XAUTHORITY')
        
        # If running via sudo/pam_exec, env might be stripped.
        # Try to find the user's GUI session.
        if not display:
            # Common default for first local user
            display = ":0"
            
        if not xauth and user:
            # Try common locations for .Xauthority
            # 1. /home/user/.Xauthority
            home_xauth = os.path.expanduser(f"~{user}/.Xauthority")
            if os.path.exists(home_xauth):
                xauth = home_xauth
            # 2. /run/user/<uid>/xauth_... (Fedora/GNOME)
            else:
                try: 
                    uid = pwd.getpwnam(user).pw_uid
                    run_dir = f"/run/user/{uid}"
                    if os.path.exists(run_dir):
                        import glob
                        matches = glob.glob(os.path.join(run_dir, "xauth_*"))
                        if matches: xauth = matches[0]
                except: pass

        gui_context = {
            "display": display,
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
