import json
import os
import urllib.request

api_base_url = os.getenv("APP_FRONTEND_API_URL") or os.getenv("VITE_API_URL")
if not api_base_url:
    raise RuntimeError("APP_FRONTEND_API_URL or VITE_API_URL must be configured")

url = f"{api_base_url.rstrip('/')}/api/interpret"
data = json.dumps({"text": "mobile"}).encode("utf-8")
req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=30) as resp:
    print(resp.read().decode('utf-8'))
