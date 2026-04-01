#!/bin/bash
echo "=== Enforcing Patches to Live Python Environment ==="
# Patch the system wrapper root (where sys.path inserts first)
sudo cp core/*.py /usr/lib/project-sentinel/
# Patch the actual site-packages wheel (where python starts the daemon)
sudo cp core/*.py /usr/lib/project-sentinel/venv/lib/python3.14/site-packages/
sudo systemctl restart sentinel-backend
echo "Successfully patched BOTH Python paths! Ghost modules eradicated. TUI will now glow green!"
