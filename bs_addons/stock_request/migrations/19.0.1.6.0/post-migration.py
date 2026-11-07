# Copyright 2026 Mohamed Elmalky
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Derive the current workflow step from existing two-step transfers."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    orders = env["stock.request.order"].search(
        [("state", "in", ("ready", "in_transit"))]
    )
    orders._sync_workflow_state_from_pickings()
