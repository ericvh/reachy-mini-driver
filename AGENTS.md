# Agent guide — Reachy Mini Device Connect

Use this when controlling Reachy over the **Device Connect portal** (NATS mesh), not the local `reachy-mini` MCP SDK.

## Connect (required pattern)

**Do not** call `device_connect_agent_tools.connect()` with only env vars unless you know they are complete.

Prefer:

```python
from reachy_mini_driver.mesh import connect_mesh, disconnect_mesh, resolve_mesh_settings, wait_for_device

zone, device_id, urls = resolve_mesh_settings(
    credentials_file="~/Downloads/your-portal.creds.json"
)
connect_mesh(credentials_file="~/Downloads/your-portal.creds.json")
wait_for_device(device_id, timeout_s=60)
# … invoke_device(device_id, "look_at_world", …) …
disconnect_mesh()
```

Or CLI examples:

```bash
export NATS_CREDENTIALS_FILE=~/Downloads/your-portal.creds.json
python examples/panorama_scan.py --credentials-file "$NATS_CREDENTIALS_FILE"
```

## Cursor MCP (`device-connect` server)

`~/.cursor/mcp.json` only needs:

```json
"device-connect": {
  "command": "/path/to/reachy-mini-driver/.venv/bin/python",
  "args": ["-m", "device_connect_agent_tools.mcp"],
  "env": {
    "NATS_CREDENTIALS_FILE": "/path/to/your-portal.creds.json"
  }
}
```

The MCP bridge loads **broker URLs, JWT, and tenant** from that file. You do **not** need `MESSAGING_BACKEND` or `MESSAGING_URLS` when using a portal `.creds.json`.

## Common failure modes (avoid the loop)

| Symptom | Cause | Fix |
|--------|--------|-----|
| `mixing of websocket and non websocket URLs` | Zenoh default `tcp://localhost:7447` mixed with portal `nats://…` | Use `connect_mesh()` or set `NATS_CREDENTIALS_FILE` to `.creds.json` (agent-tools ≥ fixed connection) |
| `permissions violation` on `device-connect.default.discovery` | Wrong tenant (used `default` instead of portal tenant) | `connect_mesh()` or `TENANT` env / tenant in creds file |
| Device not on roster | Driver not running on robot | Start `python -m reachy_mini_driver` on Reachy with portal enabled |
| `invoke_device` timeout | Robot offline or wrong `device_id` | `wait_for_device()` after connect |

## Invoke motion / audio

After connect, use MCP `invoke_device` or Python:

```python
from device_connect_agent_tools import invoke_device

invoke_device(device_id, "wake_up", {}, llm_reasoning="session start")
invoke_device(device_id, "detect_audio_activity", {"threshold": 0.04}, llm_reasoning="listen")
invoke_device(device_id, "look_at_world", {"pitch": 10, "yaw": 20}, llm_reasoning="dance")
```

Local SDK MCP (`reachy-mini`) does not use the portal; use `device-connect` for remote robots.
