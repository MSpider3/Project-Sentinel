import os
import sys
import socket
import json
import time

SOCKET_PATH = os.environ.get("SENTINEL_SOCKET_PATH", "/tmp/sentinel_test.sock")

def send_rpc(method, params={}, id_val=1):
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.settimeout(15.0)
        client.connect(SOCKET_PATH)
        req = {"jsonrpc": "2.0", "method": method, "params": params, "id": id_val}
        client.sendall((json.dumps(req) + "\n").encode('utf-8'))
        
        # Simple line reader
        buffer = ""
        while "\n" not in buffer:
            chunk = client.recv(4096).decode('utf-8')
            if not chunk: break
            buffer += chunk
            
        return json.loads(buffer.strip())
    finally:
        client.close()

def main():
    print(f"Connecting to {SOCKET_PATH}...")
    
    res = send_rpc("ping", id_val=1)
    print(f"Ping: {res}")
    
    print("Sending initialize...")
    res = send_rpc("initialize", id_val=2)
    print(f"Initialize: {res}")
    
    print("Starting Authentication (Camera Test)...")
    res = send_rpc("start_authentication", {"user": "test_user"}, id_val=3)
    print(f"Start Auth: {res}")
    
    if res.get('result', {}).get('success'):
        print("Reading frames...")
        for i in range(20):
            frame_res = send_rpc("process_auth_frame", id_val=4+i)
            success = frame_res.get('result', {}).get('success')
            err = frame_res.get('result', {}).get('error')
            state = frame_res.get('result', {}).get('state')
            print(f"Frame {i}: success={success}, error={err}, state={state}")
            time.sleep(0.5)
            
        print("Stopping Authentication...")
        res = send_rpc("stop_authentication", id_val=50)
        print(f"Stop Auth: {res}")
        
    print("Test finished.")

if __name__ == "__main__":
    main()
