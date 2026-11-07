from odoo import fields, models


class PosConfig(models.Model):
    _inherit = 'pos.config'

    tracking_employee_ids = fields.Many2many(
        'hr.employee', 'pos_config_tracking_employee_rel', 'config_id', 'employee_id',
        string="Tracking Employees",
        help="Only orders taken by these employees get the tracking QR. Leave empty for all employees.",
    )

    def _tracking_cashier_eligible(self, order):
        if self.module_pos_hr:
            return not self.tracking_employee_ids or order.employee_id in self.tracking_employee_ids
        return super()._tracking_cashier_eligible(order)
