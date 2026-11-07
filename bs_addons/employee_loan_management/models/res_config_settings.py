from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    employee_loan_account_id = fields.Many2one(
        related="company_id.employee_loan_account_id",
        readonly=False,
    )
    employee_loan_salary_input_type_id = fields.Many2one(
        related="company_id.employee_loan_salary_input_type_id",
        readonly=False,
    )
