"""Explicit incoming order-type mapping; never infer presets from their labels."""

PRESET_FIELDS = {
    # Keep the existing field so an already configured Delivery record survives upgrades.
    "delivery": "blend_default_preset_id",
    "pickup": "blend_pickup_preset_id",
    "dinein": "blend_dinein_preset_id",
}


def preset_field_for_order_type(value):
    if not isinstance(value, str):
        return None
    normalized = "".join(value.lower().split()).replace("-", "").replace("_", "")
    return PRESET_FIELDS.get(normalized)
