from odoo import fields, models


class HrSalaryAttachment(models.Model):
    _inherit = "hr.salary.attachment"

    employee_loan_installment_id = fields.Many2one(
        "employee.loan.installment",
        string="Employee Loan Installment",
        copy=False,
        index=True,
        ondelete="restrict",
    )

    _employee_loan_installment_unique = models.Constraint(
        "UNIQUE(employee_loan_installment_id)",
        "An installment can only be sent to payroll once.",
    )
