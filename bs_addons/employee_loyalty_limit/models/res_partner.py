from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    is_employee_customer = fields.Boolean(
        compute="_compute_is_employee_customer",
        compute_sudo=True,
        string="Employee Customer",
    )

    def _compute_is_employee_customer(self):
        employee_partners = set(
            self.env["hr.employee"].sudo().search(
                [("work_contact_id", "in", self.ids)]
            ).mapped("work_contact_id").ids
        )
        for partner in self:
            partner.is_employee_customer = partner.id in employee_partners

    def _load_pos_data_fields(self, config):
        return super()._load_pos_data_fields(config) + ["is_employee_customer"]
