# Reachy Mini Device Connect Driver

Standalone Reachy Mini driver for Device Connect.

The target shape is:

```text
Reachy Mini SDK / daemon
  -> reachy_mini_driver Device Connect driver
  -> Device Connect mesh
  -> device-connect-agent-tools MCP bridge
  -> Codex, Claude, or another supervised agent
```

The driver keeps an in-process state mirror for command ownership, leases,
interlocks, target pose, observed state, execution status, and errors. This
package targets the current `device_connect_edge` API directly and does not
depend on Strands Robots (see [Acknowledgments](#acknowledgments)).

## Layout

- `src/reachy_mini_driver/device_connect.py` - Device Connect `DeviceDriver`.
- `src/reachy_mini_driver/media.py` - optional SDK-backed audio/video I/O boundary.
- `src/reachy_mini_driver/transport.py` - Reachy daemon/SDK transport boundary.
- `src/reachy_mini_driver/driver_state.py` - in-process driver state tracking.
- `src/reachy_mini_driver/config.py` - Environment-driven runtime config.
- `src/reachy_mini_driver/__main__.py` - CLI runner.
- `src/reachy_mini_driver/reachy_app.py` - ReachyMiniApp (HF app store / dashboard entry).
- `src/reachy_mini_driver/runtime_launcher.py` - Shared CLI and app startup.
- `src/reachy_mini_driver/app_settings.py` - JSON settings for the dashboard app UI.
- `src/reachy_mini_driver/static/` - Embedded settings HTML for the Reachy app.
- `device-connect-driver/` - Hugging Face Spaces wrapper (`reachy-mini-app-assistant publish`); see `device-connect-driver/README.md`.
- `examples/claude_desktop_config.json` - MCP bridge config shape.
- `tests/` - hardware-free unit tests.

Python **3.11–3.12** are recommended (**3.13** often works; **3.14** is unsupported for SDK media deps). Use `**pip install -e ".[media]"`** when you need camera/mic Device Connect functions.

## Bring-Up

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

DEVICE_CONNECT_ALLOW_INSECURE=true \
python -m reachy_mini_driver --host reachy-mini.local --device-id reachy-mini-1
```

For driver bring-up without robot hardware, use the built-in simulated target:

```bash
DEVICE_CONNECT_ALLOW_INSECURE=true \
python -m reachy_mini_driver --sim --device-id reachy-mini-sim-1
```

If mDNS/Avahi is not available, avoid `reachy-mini.local` and set the target
explicitly by IP address or resolvable hostname:

```bash
DEVICE_CONNECT_ALLOW_INSECURE=true \
python -m reachy_mini_driver --target 192.168.4.12:8000 --device-id reachy-mini-1
```

The same target selection is available through environment variables:

```bash
REACHY_TARGET=192.168.4.12:8000 python -m reachy_mini_driver
REACHY_TARGET=sim python -m reachy_mini_driver
REACHY_SIM=true python -m reachy_mini_driver
```

To expose the device to an MCP client, run the Device Connect MCP bridge from
`device-connect-agent-tools`:

```bash
python -m device_connect_agent_tools.mcp
```

The MCP client should see `describe_fleet`, `list_devices`,
`get_device_functions`, and `invoke_device`.

## Reachy Mini app (dashboard + HF Spaces)

You can install the driver as a **Reachy Mini application** (`ReachyMiniApp`) so it is started from the on-robot dashboard and can be packaged as a Hugging Face Space ([community guide](https://huggingface.co/blog/pollen-robotics/make-and-publish-your-reachy-mini-apps)).

Install on the robot (or your dev venv):

```bash
pip install -e ".[app,media]"
```

- **Entry point** (shown in the app list): `reachy_mini_device_connect` (`pyproject.toml` → `[project.entry-points."reachy_mini_apps"]`).
- **Process entry**: `python -m reachy_mini_driver.reachy_app` — the Reachy launcher runs this for installed apps.

While the app is running, **settings and validation** are served on `**http://<robot-ip>:8842`** (`GET/PUT /api/settings`, `POST /api/validate`). The bundled page edits the same JSON as:

```text
~/.config/reachy_mini_driver/device_connect_app.json
```

**Typical robot setup:** open the settings page (`http://<robot-ip>:8842`), enable portal mode, upload `**.json`** or `**.creds**` (or set `**nats_credentials_file**`), then set `**reachy_target**` `127.0.0.1:8000` and `**transport_mode**` `websocket`. Portal mode is **off** by default so the app can start before credentials exist.

Environment variables documented in `config.py` (for example `NATS_CREDENTIALS_FILE`, `DEVICE_CONNECT_PORTAL`) still layer on top of the JSON file—use env for secrets injected by your process manager.

**Logs:** the driver prints timestamped lines to stderr (config banner, transport connect, joint stream, and a heartbeat every 30s while running). Set `REACHY_MINI_DRIVER_LOG_LEVEL=DEBUG` for more detail from `device_connect_edge`.

To publish your own HF Space repo, build `device-connect-driver/pyproject.toml` with
`device-connect-driver/sync_driver_dependency.py`, run `reachy-mini-app-assistant check device-connect-driver`, then
see `device-connect-driver/README.md`.

## Exposed Device Functions

The current driver exposes:

- `get_status`
- `get_joints`
- `get_imu`
- `look_at_world`
- `antenna_pose`
- `goto_sleep`
- `wake_up`
- `stop_motion`
- `get_media_status`
- `detect_audio_activity`
- `detect_motion`
- `capture_video_frame`
- `push_video_frame`
- `start_audio_input`
- `stop_audio_input`
- `capture_audio_sample`
- `start_audio_output`
- `stop_audio_output`
- `play_audio_file`
- `push_audio_sample`

The media path is optional. Without the Reachy Mini SDK media dependencies,
media functions return explicit `unsupported` or `error` responses instead of
silently pretending media is available.

The driver also exposes low-level audio and motion events:

- `audio_event`
- `motion_event`
- `safety_event`

`detect_audio_activity` samples microphone input and emits `audio_event` with
RMS, threshold, activity state, and confidence. `detect_motion` samples video
input and emits `motion_event` with a frame-delta magnitude, threshold, state,
and confidence. These events carry evidence, not ASR transcripts or semantic
vision labels.

## Video over Device Connect

Device Connect RPC replies travel over **NATS**, which enforces a small maximum
message size (often about **1 MiB** on portal brokers). A single **raw** camera
frame (RGB + base64 + JSON) can exceed that and stall or reset the connection.

### Current behavior (`capture_video_frame`)

| `encoding` | Use |
|------------|-----|
| **`jpeg`** (default) | Resize (longest side ≤ 640 px) + JPEG (~82 quality). Safe for portal/MCP. |
| **`thumbnail`** | Smaller preset (≤ 320 px, slightly lower quality). |
| **`raw`** | Full numpy buffer in the RPC body. **Local/dev only**; rejected when the encoded JSON would exceed ~900 KiB unless you explicitly need raw and accept `nats_payload_warning`. |

Optional parameters: `max_edge`, `quality` (JPEG 1–95).

`detect_motion` still processes **full frames on the robot** and only returns a
small JSON result over NATS (no full image in the RPC).

Requires the **`[media]`** extra (`Pillow` is used for JPEG). Install with:

```bash
pip install -e ".[app,media]"
```

### Direct stream access (implemented RPCs)

When an agent needs **full resolution** or **live** video/audio, call these
Device Connect functions. They return **metadata only** (URLs, SDK kwargs)—not
media bytes over NATS.

| RPC | Purpose |
|-----|---------|
| **`get_media_stream_access`** | Connection hints: WebRTC signaling URL (`ws://<robot>:8443`), local GStreamer IPC (on-robot only), daemon API URL. Optional `probe_signaling=true` checks that the `reachymini` WebRTC producer is up (needs `reachy_mini` installed on the driver host). |
| **`release_media_hardware`** | `POST /api/media/release` — daemon stops owning camera/mic so on-robot code can use OpenCV / sounddevice directly. |
| **`acquire_media_hardware`** | `POST /api/media/acquire` — return devices to the daemon pipeline. |

**Remote client (typical portal user):** use the `webrtc` backend from the RPC
response—connect with `ReachyMini(host=<robot-ip>, media_backend="webrtc")` or
`GstWebRTCClient` against `ws://<robot-ip>:8443` (producer name `reachymini`).
Your machine must reach port **8443** on the robot (LAN/VPN/firewall).

**Driver on the robot** (`REACHY_TARGET=127.0.0.1:8000`): prefer `local` when
`/tmp/reachymini_camera_socket` exists (no WebRTC encode/decode for video).

**Direct hardware on robot:** call `release_media_hardware`, open devices locally,
then `acquire_media_hardware` when finished.

On-robot deployments, realtime state already uses **WebSocket/Zenoh** to the
daemon (`transport_mode=websocket` on the robot). Device Connect remains the
**control plane**; bulk video should not share the same NATS subjects as RPC.

**Not implemented here:** HTTP snapshot URLs, object-store upload links, or TURN
setup—use WebRTC or JPEG RPC unless you add those side channels yourself.

### Future work / anti-patterns

Do **not** rely on these for portal use until explicitly designed:

- Default **raw** `data_b64` in every `capture_video_frame` call over the portal.
- **Chunked NATS** frame reassembly (complex; prefer JPEG RPC + HTTP side channel).
- **WebRTC inside every RPC** for a single snapshot (overkill vs JPEG thumbnail).

## Target Modes

- Hardware by mDNS: `--host reachy-mini.local` or `REACHY_HOST=reachy-mini.local`.
- Hardware without Avahi: `--target <host-or-ip>:<port>` or `REACHY_TARGET=<host-or-ip>:<port>`.
- Driver-level simulation: `--sim`, `--target sim`, `REACHY_TARGET=sim`, or `REACHY_SIM=true`.

The simulated target is a deterministic driver-level target for developing the
Device Connect and MCP path. It does not claim to be the official Reachy Mini
physics simulator. It provides fake daemon status, joint state, IMU state,
bounded motion acceptance, and simulated audio/video input/output payloads.

## Smoke Tests

Run the hardware-free unit tests:

```bash
PYTHONPATH=src:/path/to/device-connect/packages/device-connect-edge:/path/to/device-connect/packages/device-connect-agent-tools \
python3 -m unittest discover -s tests -v
```

Run the simulated DeviceRuntime smoke test:

```bash
PYTHONPATH=src:/path/to/device-connect/packages/device-connect-edge:/path/to/device-connect/packages/device-connect-agent-tools \
python3 tests/smoke_sim_runtime.py
```

The smoke script constructs `DeviceRuntime` around the simulated driver,
verifies generated Device Connect function/event schemas, invokes functions
through `DeviceDriver.invoke`, and checks simulated audio/motion events. It does
not start a broker-backed Device Connect mesh or the MCP bridge.

Run the optional broker-backed hierarchical tool smoke test:

```bash
MESSAGING_BACKEND=nats MESSAGING_URLS=nats://127.0.0.1:4222 \
PYTHONPATH=src:/path/to/device-connect/packages/device-connect-edge:/path/to/device-connect/packages/device-connect-agent-tools \
python3 tests/smoke_broker_mcp_tools.py
```

This starts the simulated driver as a `DeviceRuntime`, then calls the same
hierarchical Python tool functions exposed by the MCP bridge:
`describe_fleet`, `list_devices`, `get_device_functions`, and `invoke_device`.
It requires a reachable Device Connect broker.

Run the optional stdio MCP client/server smoke test:

```bash
pip install -e "/path/to/device-connect/packages/device-connect-agent-tools[mcp]"

MESSAGING_BACKEND=nats MESSAGING_URLS=nats://127.0.0.1:4222 \
PYTHONPATH=src:/path/to/device-connect/packages/device-connect-edge:/path/to/device-connect/packages/device-connect-agent-tools \
python3 tests/smoke_stdio_mcp_bridge.py
```

This starts the simulated driver as a `DeviceRuntime`, launches
`python -m device_connect_agent_tools.mcp` over stdio, then calls MCP
`tools/list` and `tools/call` for `list_devices`, `get_device_functions`, and
`invoke_device`.

### Local Broker

Use the automation script to manage a local NATS-backed smoke environment:

```bash
.venv/bin/python scripts/local_nats_smoke.py start
.venv/bin/python scripts/local_nats_smoke.py status
.venv/bin/python scripts/local_nats_smoke.py broker-smoke
.venv/bin/python scripts/local_nats_smoke.py stdio-smoke
.venv/bin/python scripts/local_nats_smoke.py stop
```

To start services, run the broker-backed smoke, and stop services in one step:

```bash
.venv/bin/python scripts/local_nats_smoke.py all
```

Include the stdio MCP smoke as well:

```bash
.venv/bin/python scripts/local_nats_smoke.py all --stdio
```

The script uses `~/src/device-connect/tests/docker-compose-itest.yml` by
default and starts `nats`, `etcd`, and `device-registry-service`. It sets the
child smoke environment to NATS plus registry-backed discovery:
`MESSAGING_BACKEND=nats`, `MESSAGING_URLS=nats://127.0.0.1:4222`,
`DEVICE_CONNECT_DISCOVERY_MODE=infra`, and `TENANT=default`.

Override the checkout location or tenant with:

```bash
.venv/bin/python scripts/local_nats_smoke.py --device-connect-root /path/to/device-connect start
.venv/bin/python scripts/local_nats_smoke.py all --tenant lab --device-id reachy-mini-sim-lab
```

The broker smoke requires `nats` and `nkeys` Python packages. The stdio MCP
smoke also requires the optional MCP bridge dependency:

```bash
.venv/bin/python -m pip install -e "$HOME/src/device-connect/packages/device-connect-agent-tools[mcp]"
```

The equivalent manual Docker Compose commands are:

```bash
cd /path/to/device-connect/tests
docker compose -f docker-compose-itest.yml up -d nats etcd device-registry-service
docker compose -f docker-compose-itest.yml down -v --remove-orphans
```

## Acknowledgments

**Waheed Brown** (`[armwaheed/robots](https://github.com/armwaheed/robots)`) — Reachy Mini Device Connect work in
`[strands_robots/device_connect](https://github.com/armwaheed/robots/tree/reachy-mini/strands_robots/device_connect)`
(`reachy_mini_driver.py`, `reachy_transport.py`, and related deployment notes) was valuable prior art for
Reachy transport selection (HTTP, WebSocket, Zenoh), real-time I/O topic layout, and motion/status RPC
boundaries. This driver is a standalone implementation on `device_connect_edge` and is not part of or
dependent on Strands Robots; we are grateful for that earlier exploration.