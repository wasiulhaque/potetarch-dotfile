import json
import subprocess

try:
    result = subprocess.run(
        ["tailscale", "status", "--json", "--peers=false"],
        capture_output=True,
        text=True,
        timeout=3,
    )
    state = json.loads(result.stdout).get("BackendState")
except (OSError, ValueError, subprocess.TimeoutExpired):
    state = None

labels = {
    "Running": "Connected",
    "Starting": "Connecting",
    "Stopped": "Disconnected",
    "NeedsLogin": "Sign-in required",
    "NeedsMachineAuth": "Device approval required",
}
print(json.dumps({
    "text": " ",
    "tooltip": f"Tailscale · {labels.get(state, 'Unavailable')}",
    "class": "connected" if state == "Running" else "disconnected",
}))
