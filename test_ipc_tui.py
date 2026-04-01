import logging
import sys
import os

# Set up logging to catch ALL debug and error messages
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s | %(name)s | %(levelname)s | %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

from sentinel_tui.services.ipc_client import SentinelIPCClient

print("--- Initializing strict TUI IPC Client ---")
client = SentinelIPCClient("/run/sentinel/sentinel.sock", debug=True)

print("--- Connecting to daemon ---")
connected = client.connect()
print(f"Connected: {connected}")

if not connected:
    print("Failed to connect.")
    sys.exit(1)

print("\n--- Testing 'health' API ---")
res = client.call("health")
print(f"Health Response: {res}")

print("\n--- Testing 'get_devices' API ---")
res2 = client.call("get_devices")
print(f"Devices Response: {res2}")

print("\n--- Disconnecting ---")
client.disconnect()
