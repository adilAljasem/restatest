"""Read the original Blend total without treating missing data as zero."""
import math


def blend_payload_total(payload):
    if not isinstance(payload, dict):
        return None
    value = payload.get("total")
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None
