from odoo import api, models


class IrUiMenu(models.Model):
    _inherit = "ir.ui.menu"

    @api.model
    def _visible_menu_ids(self, debug=False):
        visible_ids = super()._visible_menu_ids(debug=debug)
        diagnostics_menu_id = self.env["ir.model.data"]._xmlid_to_res_id(
            "bs_pos_kitchen_flow.kitchen_flow_log_menu",
            raise_if_not_found=False,
        )
        if (
            diagnostics_menu_id
            and (not debug or not self.env.user.has_group("base.group_system"))
        ):
            return visible_ids - {diagnostics_menu_id}
        return visible_ids
