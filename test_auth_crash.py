import sys
print("starting...")
sys.path.insert(0, 'core')

import cv2
import numpy as np
from core.sentinel_service import SentinelService

print("init service")
s = SentinelService()
print("starting auth")
s.start_authentication({})

print("crafting frame")
# Manually put a frame in the camera
import time
time.sleep(1)

# Grab the traceback
for _ in range(3):
    print("processing frame...")
    res = s.process_auth_frame({})
    if not res.get("success"):
        print("ERROR IN AUTH:")
        print(res)
    else:
        print("Auth success result")

print("done")
