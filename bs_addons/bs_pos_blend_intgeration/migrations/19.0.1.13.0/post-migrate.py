import logging

from odoo import SUPERUSER_ID, api


_logger = logging.getLogger(__name__)


def _normalized_limits(record):
    minimum = max(record.blend_min_selection or 0, 0)
    maximum = max(record.blend_max_selection or 0, minimum, 1)
    maximum_per_quantity = max(record.blend_max_per_quantity or 0, 1)
    return {
        "blend_min_selection": minimum,
        "blend_max_selection": maximum,
        "blend_max_per_quantity": maximum_per_quantity,
    }


def migrate(cr, version):
    """Make every modifier selection maximum explicit and positive."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    context = {"blend_inbound_sync": True, "tracking_disable": True}
    normalized = 0

    for model_name in ("modifiers.groups", "product.modifiers"):
        records = env[model_name].sudo().search([])
        for record in records:
            values = _normalized_limits(record)
            if any(record[field_name] != value for field_name, value in values.items()):
                record.with_context(**context).write(values)
                normalized += 1

    _logger.info("Normalized positive Blend selection limits on %s record(s)", normalized)
