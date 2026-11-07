# Copyright 2026 Mohamed Elmalky
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Stop Stock Transfer from extending Odoo's standard warehouse form."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    legacy_view = env.ref(
        "stock_request.view_warehouse_inherit_bool_is_project_wh",
        raise_if_not_found=False,
    )
    if legacy_view:
        legacy_view.unlink()
