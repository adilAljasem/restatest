"""Move existing TryOrder POS orders to the branch's Delivery preset."""

import logging
from collections import defaultdict

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    orders = env["bs.delivery.order"].with_context(active_test=False).search([
        ("provider_id.code", "=", "tryorder"),
        ("pos_order_id", "!=", False),
        ("store_binding_id", "!=", False),
    ])
    by_preset = defaultdict(list)
    for order in orders:
        preset = order._pos_order_preset(order.store_binding_id)
        if preset and order.pos_order_id.preset_id != preset:
            by_preset[preset.id].append(order.pos_order_id.id)
    moved = 0
    for preset_id, pos_order_ids in by_preset.items():
        # Plain SQL: paid and invoiced POS orders refuse ORM writes, and only
        # the preset (the POS tab and the ticket's order type) changes.
        cr.execute(
            "UPDATE pos_order SET preset_id = %s WHERE id IN %s",
            [preset_id, tuple(pos_order_ids)],
        )
        moved += cr.rowcount
    _logger.info("Moved %s TryOrder POS orders to the Delivery preset", moved)
