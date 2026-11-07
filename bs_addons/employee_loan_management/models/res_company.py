from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    employee_loan_account_id = fields.Many2one(
        "account.account",
        string="Employee Loan Account",
        check_company=True,
        domain="[('active', '=', True)]",
    )
    employee_loan_salary_input_type_id = fields.Many2one(
        "hr.payslip.input.type",
        string="Loan Salary Input Type",
        domain="[('available_in_attachments', '=', True)]",
    )
