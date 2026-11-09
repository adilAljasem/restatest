from odoo import api, models


class IrUiMenu(models.Model):
    _inherit = "ir.ui.menu"

    @api.model
    def _visible_menu_ids(self, debug=False):
        """Hide the menu trees of locked features (UI only; model access untouched)."""
        visible = super()._visible_menu_ids(debug)
        Feature = self.env["resta.growth.feature"]
        if Feature._user_bypasses_lock():
            return visible
        locked_menus, _actions = Feature._locked_ids()
        return visible - locked_menus if locked_menus else visible
