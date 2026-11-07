import json
import re


MASK = "********"
MAX_PAYLOAD_CHARS = 64_000

_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "authtoken",
        "accesstoken",
        "refreshtoken",
        "token",
        "apikey",
        "secret",
        "secretkey",
        "clientsecret",
        "password",
        "passwd",
        "cookie",
        "setcookie",
        "jwt",
        "webhooksecret",
    }
)
_KEY_VALUE_RE = re.compile(
    r"(?i)(authorization|api[-_ ]?key|auth[-_ ]?token|access[-_ ]?token|refresh[-_ ]?token|"
    r"client[-_ ]?secret|secret(?:[-_ ]?key)?|password|passwd|cookie|jwt|"
    r"[a-z0-9_-]*signature)"
    r"(\s*[\"']?\s*[:=]\s*[\"']?)"
    r"([^&\s,}\]\"']+|Bearer\s+[^&\s,}\]\"']+)"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")


def _normalized_key(key):
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _is_sensitive_key(key):
    normalized = _normalized_key(key)
    return (
        normalized in _SENSITIVE_KEYS
        or normalized.endswith("signature")
        or normalized.endswith("apikey")
    )


def mask_sensitive_data(value):
    """Recursively replace values whose keys identify credentials."""
    if isinstance(value, dict):
        return {
            str(key): MASK if _is_sensitive_key(key) else mask_sensitive_data(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [mask_sensitive_data(item) for item in value]
    return value


def mask_sensitive_text(value):
    """Mask common credentials in URLs, headers, and non-JSON strings."""
    if value in (None, False):
        return value
    text = str(value)
    text = _BEARER_RE.sub("Bearer %s" % MASK, text)
    return _KEY_VALUE_RE.sub(lambda match: "%s%s%s" % (match[1], match[2], MASK), text)


def serialize_masked_payload(payload, *, max_chars=MAX_PAYLOAD_CHARS):
    """Return a bounded, masked text representation suitable for database logs."""
    if payload in (None, False, ""):
        return False

    parsed = payload
    if isinstance(payload, (bytes, bytearray)):
        parsed = bytes(payload).decode("utf-8", errors="replace")
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except (TypeError, ValueError):
            result = mask_sensitive_text(parsed)
        else:
            result = json.dumps(
                mask_sensitive_data(parsed),
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
    else:
        result = json.dumps(
            mask_sensitive_data(parsed),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )

    if len(result) > max_chars:
        return "%s\n...[truncated]" % result[:max_chars]
    return result
