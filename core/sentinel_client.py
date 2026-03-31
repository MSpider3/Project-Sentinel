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
        
        # Request
        req = {
            "jsonrpc": "2.0",
            "method": "authenticate_pam",
            "params": {"user": user},
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
