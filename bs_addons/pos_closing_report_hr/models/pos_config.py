from odoo import api, fields, models


class PosConfig(models.Model):
    _inherit = "pos.config"

    pcr_day_employee_ids = fields.Many2many(
        "hr.employee", "pos_config_pcr_day_employee_rel", "config_id", "employee_id",
        string="Employees Allowed to Print the Business Day Report",
        help="Leave empty (with no allowed users): POS managers. Otherwise only these employees "
        "can print the business day report from the POS.",
    )

    def _pcr_day_allowed(self):
        allowed = super()._pcr_day_allowed()
        allowed["employees"] = self.sudo().pcr_day_employee_ids.ids
        return allowed

    @api.depends("pcr_day_user_ids", "pcr_day_employee_ids")
    def _compute_pcr_day_access(self):
        return super()._compute_pcr_day_access()
