"""Encode camera frames for Device Connect RPC (NATS payload limits)."""

from __future__ import annotations

import base64
import json
from typing import Any

# Conservative cap for portal/cloud NATS RPC replies (JSON + base64 overhead).
DEFAULT_MAX_RPC_PAYLOAD_BYTES = 900_000

SUPPORTED_ENCODINGS = frozenset({"jpeg", "thumbnail", "raw"})

_ENCODING_PRESETS: dict[str, dict[str, int]] = {
    "jpeg": {"max_edge": 640, "jpeg_quality": 82},
    "thumbnail": {"max_edge": 320, "jpeg_quality": 75},
}


def encode_video_frame_payload(
    array: Any,
    *,
    encoding: str = "jpeg",
    max_edge: int | None = None,
    quality: int | None = None,
    max_payload_bytes: int = DEFAULT_MAX_RPC_PAYLOAD_BYTES,
    allow_oversized_raw: bool = False,
) -> dict[str, Any]:
    """Build a JSON-serializable video frame dict within *max_payload_bytes* when possible."""
    normalized = encoding.strip().lower()
    if normalized not in SUPPORTED_ENCODINGS:
        return {
            "status": "error",
            "reason": f"unsupported encoding {encoding!r}; use jpeg, thumbnail, or raw",
        }

    try:
        import numpy as np

        data = np.asarray(array)
    except Exception as exc:
        return {"status": "error", "reason": f"invalid frame array: {exc}"}

    if normalized == "raw":
        payload = _raw_payload(data)
        return _finalize_payload(
            payload,
            max_payload_bytes=max_payload_bytes,
            allow_oversized=allow_oversized_raw,
            encoding="raw",
        )

    preset = _ENCODING_PRESETS[normalized]
    edge = max_edge if max_edge is not None else preset["max_edge"]
    jpeg_q = quality if quality is not None else preset["jpeg_quality"]
    try:
        payload = _jpeg_payload(
            data, max_edge=edge, quality=jpeg_q, encoding_label=normalized
        )
    except RuntimeError as exc:
        return {"status": "error", "reason": str(exc)}
    return _finalize_payload(
        payload,
        max_payload_bytes=max_payload_bytes,
        allow_oversized=False,
        encoding=normalized,
    )


def encode_from_media_result(
    media_result: dict[str, Any],
    *,
    encoding: str = "jpeg",
    max_edge: int | None = None,
    quality: int | None = None,
    max_payload_bytes: int = DEFAULT_MAX_RPC_PAYLOAD_BYTES,
    allow_oversized_raw: bool = False,
) -> dict[str, Any]:
    """Encode a :meth:`~reachy_mini_driver.media.MediaClient.get_video_frame` result."""
    if media_result.get("status") != "success":
        return media_result
    shape = media_result.get("shape")
    if not shape:
        return {
            "status": "error",
            "reason": "media frame missing dtype/shape metadata; cannot encode for RPC",
        }
    try:
        import numpy as np

        raw = base64.b64decode(media_result.get("data_b64", ""))
        dtype = np.dtype(media_result.get("dtype", "uint8"))
        array = np.frombuffer(raw, dtype=dtype).reshape(shape)
    except Exception as exc:
        return {"status": "error", "reason": f"could not decode media frame: {exc}"}
    encoded = encode_video_frame_payload(
        array,
        encoding=encoding,
        max_edge=max_edge,
        quality=quality,
        max_payload_bytes=max_payload_bytes,
        allow_oversized_raw=allow_oversized_raw,
    )
    if encoded.get("status") == "success" and media_result.get("target"):
        encoded["target"] = media_result["target"]
    return encoded


def _raw_payload(array: Any) -> dict[str, Any]:
    import numpy as np

    data = np.asarray(array)
    return {
        "status": "success",
        "kind": "video_frame",
        "encoding": "raw",
        "dtype": str(data.dtype),
        "shape": list(data.shape),
        "data_b64": base64.b64encode(data.tobytes()).decode("ascii"),
    }


def _jpeg_payload(
    array: Any, *, max_edge: int, quality: int, encoding_label: str = "jpeg"
) -> dict[str, Any]:
    import numpy as np
    from PIL import Image

    data = np.asarray(array)
    if data.ndim == 3 and data.shape[2] == 1:
        data = data[:, :, 0]
    if data.ndim == 2:
        image = Image.fromarray(data, mode="L")
    elif data.ndim == 3 and data.shape[2] == 3:
        image = Image.fromarray(data.astype(np.uint8, copy=False), mode="RGB")
    elif data.ndim == 3 and data.shape[2] == 4:
        image = Image.fromarray(data.astype(np.uint8, copy=False), mode="RGBA").convert("RGB")
    else:
        raise RuntimeError(f"unsupported frame shape for jpeg: {data.shape}")

    if max(data.shape[0], data.shape[1]) > max_edge:
        image = _resize_image(image, max_edge)

    from io import BytesIO

    buf = BytesIO()
    image.save(buf, format="JPEG", quality=max(1, min(quality, 95)))
    jpeg_bytes = buf.getvalue()
    width, height = image.size
    return {
        "status": "success",
        "kind": "video_frame",
        "encoding": encoding_label,
        "format": "jpeg",
        "width": width,
        "height": height,
        "jpeg_quality": quality,
        "byte_size": len(jpeg_bytes),
        "data_b64": base64.b64encode(jpeg_bytes).decode("ascii"),
    }


def _resize_image(image: Any, max_edge: int) -> Any:
    width, height = image.size
    if max(width, height) <= max_edge:
        return image
    scale = max_edge / max(width, height)
    new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
    from PIL import Image

    return image.resize(new_size, Image.Resampling.LANCZOS)


def _finalize_payload(
    payload: dict[str, Any],
    *,
    max_payload_bytes: int,
    allow_oversized: bool,
    encoding: str,
) -> dict[str, Any]:
    if payload.get("status") != "success":
        return payload
    size = _estimate_json_bytes(payload)
    payload["rpc_json_bytes"] = size
    if size <= max_payload_bytes:
        return payload
    if allow_oversized and encoding == "raw":
        payload["nats_payload_warning"] = (
            f"raw frame ({size} bytes JSON) may exceed portal NATS limits; "
            "prefer encoding=jpeg or thumbnail over Device Connect"
        )
        return payload
    return {
        "status": "error",
        "reason": (
            f"encoded frame too large for Device Connect RPC ({size} bytes > "
            f"{max_payload_bytes} limit). Use encoding='jpeg' or 'thumbnail', or fetch "
            "video over a side channel (see README — Video over Device Connect)."
        ),
        "encoding": encoding,
        "rpc_json_bytes": size,
        "max_payload_bytes": max_payload_bytes,
    }


def _estimate_json_bytes(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
