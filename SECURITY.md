# Security

Threat model and deployment guidance for **reachy-mini-driver**. This document
covers this package and how it sits in the Device Connect stack; broker ACLs,
portal issuance, and MCP client supervision live in upstream components unless
noted.

## Scope and deployment shapes

| Shape | Driver runs on | Typical control path | Main exposure |
|-------|----------------|----------------------|---------------|
| **Dev / lab** | Laptop | Local NATS + `DEVICE_CONNECT_ALLOW_INSECURE=true` | Open mesh, plain HTTP to robot |
| **On-robot app** | Reachy | Dashboard settings UI + loopback daemon | LAN settings UI, portal creds on disk |
| **Portal / remote** | Robot or edge host | `nats://portal.deviceconnect.dev` + `.json`/`.creds` | Anyone with mesh credentials can invoke RPCs |

The driver is **not** a safety-certified robot controller. It forwards bounded
motion and media commands to the Reachy daemon and mirrors state for agents.

## Trust boundaries

```text
  [Human supervisor]     [LLM agent host]
         |                        |
         | supervises             | MCP stdio / IDE
         v                        v
              device-connect-agent-tools (invoke_device)
                        |
                        | NATS (portal or local broker)
                        v
              reachy_mini_driver (this package)
                        |
          +-------------+-------------+
          |                           |
   HTTP/WS :8000 (daemon)      ws://:8443 WebRTC (media)
          |                           |
          v                           v
     Reachy Mini hardware      camera / mic / speaker
```

**Settings UI (on-robot app only):** `http://0.0.0.0:8842` — separate from Device
Connect; any host that can reach the robot LAN may read or change app JSON and
upload portal credential files.

## Assets

| Asset | Why it matters |
|-------|----------------|
| **Physical robot** | Motion can collide with people or objects; sleep/wake affects availability |
| **Camera / microphone** | Privacy-sensitive; frames and audio samples leave the robot via RPC or side channels |
| **Portal / NATS credentials** | Grant mesh membership for `invoke_device` on registered devices |
| **Driver settings file** | `~/.config/reachy_mini_driver/device_connect_app.json` — portal paths, `allow_insecure`, targets |
| **Command ownership metadata** | `command_owner` / lease fields in `driver_state` (advisory, see below) |

## Threat actors (representative)

1. **Compromised or mis-prompted agent** — calls MCP tools repeatedly (motion spam, audio playback, panorama scans).
2. **Mesh participant with valid NATS credentials** — same RPC surface as the MCP bridge without going through your IDE.
3. **LAN client** — reaches daemon `:8000`, WebRTC `:8443`, or settings `:8842` without portal access.
4. **Settings UI attacker** — uploads credentials, enables portal, toggles `allow_insecure` if the port is reachable.
5. **Credential thief** — reads credential files from disk or backups.

## Controls in this package

| Control | What it does | Limitation |
|---------|----------------|------------|
| **Kinematic RPC bounds** | `look_at_world`, `set_body_yaw`, `antenna_pose` clamp angles/ranges before daemon I/O | Does not replace workspace fencing or human oversight |
| **`assert_motion_allowed()`** | Blocks motion when `interlock_state != "safe"` | Interlock is only checked here; nothing in-tree sets a non-`safe` interlock today |
| **Lease / `command_owner` fields** | Recorded on motion RPCs (`set_target`) and exposed in `get_status` | **Not enforced** — a new caller can issue motion without waiting for lease expiry |
| **`safety_event` emit** | Hook for upstream to observe rejections | Emission path exists; interlock-driven stops are not wired |
| **JPEG / thumbnail encoding** | Caps RPC payload size (~900 KiB JSON) for portal NATS stability | Reduces accidental broker DoS; not a confidentiality control |
| **`raw` encoding guard** | Rejects oversized raw frames unless explicitly allowed with warning | Dev-only; can still harm local NATS |
| **Credential upload limits** | `.json`/`.creds` only, 256 KiB max, basename-only filename | Does not encrypt at rest or authenticate the uploader |
| **Portal default off** | App starts without cloud mesh until configured | Misconfiguration via env or UI still possible |
| **`allow_insecure` default false** | Passed to `device_connect_edge.DeviceRuntime` | README examples and MCP sample config use insecure mode for local dev |
| **Low-level events** | `audio_event` / `motion_event` expose RMS/delta metrics, not ASR or object labels | Reduces accidental semantic leakage over NATS; does not hide raw media RPCs |

## Inherited and external controls

Rely on these outside this repository:

- **Device Connect portal** — credential issuance, tenant/device binding, broker TLS (when not using `allow_insecure`).
- **NATS account permissions** — which subjects a principal may publish/subscribe.
- **MCP host policy** — which tools the agent may call, rate limits, human approval.
- **Network segmentation** — firewall robot ports (`8000`, `8443`, `8842`) from untrusted Wi‑Fi.
- **Reachy daemon** — authoritative motion stack and hardware interlocks (if enabled in firmware/daemon).

## STRIDE summary

| Category | Example threat | Mitigation today | Residual risk |
|----------|----------------|------------------|---------------|
| **Spoofing** | Caller poses as another `owner` string on RPC | Owner is an unauthenticated string parameter | Audit logs only; no cryptographic caller identity |
| **Tampering** | LAN client changes settings or daemon API | No auth on settings UI or plain HTTP daemon | Restrict LAN; restart driver after trusted config |
| **Repudiation** | Agent denies ordering a motion | Driver logs to stderr; no append-only audit bus | Enable centralized logging if required |
| **Information disclosure** | `capture_video_frame` / `capture_audio_sample` over mesh | Prefer JPEG/thumbnail; use WebRTC side channel for HD | Any mesh member with invoke rights can request media |
| **Denial of service** | Large `raw` frames or tight motion loops | Payload size checks; bounded angle ranges | Broker or daemon can still be stressed |
| **Elevation** | Upload creds via `:8842`, join portal, control robot | File type/size checks only | Treat settings port like root on robot config |

## Deliberate tradeoffs

1. **Agent-first control plane** — Device Connect exposes many RPCs so supervised agents can operate the robot. Convenience trades off **least privilege**; narrow MCP allowlists and broker ACLs in production.
2. **Advisory leases** — `command_owner` / `lease_expires_at` support observability and future coordination but **do not arbitrate** concurrent mesh callers. Multi-agent fleets need an external orchestrator or upstream Device Connect policy.
3. **Unauthenticated settings UI** — Simplifies robot bring-up (upload `.creds`, set `127.0.0.1:8000`). Acceptable only on a **trusted LAN**; do not expose `8842` to the internet or guest Wi‑Fi.
4. **Plain HTTP / WS to daemon** — Matches Reachy’s default local API. On-robot `127.0.0.1:8000` limits remote tampering; remote `REACHY_TARGET` over LAN is trust-on-LAN.
5. **WebRTC and GStreamer side channels** — `get_media_stream_access` intentionally moves bulk video off NATS. Firewall and VPN policy must cover `:8443` (and local IPC paths on-robot).
6. **`DEVICE_CONNECT_ALLOW_INSECURE`** — Documented for local smoke tests and `examples/claude_desktop_config.json`. **Disable** for portal or any shared broker.
7. **Simulation target** — `--sim` does not touch hardware but still registers a device on the mesh if connected; use distinct `device_id` values to avoid operator confusion.

## Recommended deployments

| Goal | Suggestion |
|------|------------|
| **Production portal** | Portal on, `allow_insecure=false`, credentials via env (not committed), unique `device_id`, broker ACLs reviewed |
| **On-robot app** | Bind settings UI to LAN firewall rules; upload creds once, then restrict `:8842`; keep `reachy_target=127.0.0.1:8000` |
| **Dev laptop** | Local NATS only, sim device id, insecure flag acceptable on loopback |
| **Media privacy** | Default to `jpeg`/`thumbnail` over mesh; WebRTC only to known clients; disable `[media]` if camera/mic RPCs are not needed |
| **Multi-agent** | Single supervisor or external mutex; do not assume driver leases serialize callers |

## Out of scope (today)

- TLS termination for daemon or settings UI inside this package
- Enforcing motion leases or programmatic interlock trips
- TURN/STUN hardening for WebRTC through restrictive NATs
- Content moderation for `play_audio_file` paths
- Automated credential rotation or HSM storage

For upstream mesh security, see Device Connect and `device-connect-agent-tools`
documentation. For robot-side safety, follow Pollen Robotics operational guidance
for Reachy Mini.
