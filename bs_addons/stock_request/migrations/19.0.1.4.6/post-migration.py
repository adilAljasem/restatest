# Copyright 2026 Mohamed Elmalky
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Grant Direct Transfer access to existing warehouse responsibles."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["stock.warehouse"]._sync_stock_request_direct_transfer_group()
