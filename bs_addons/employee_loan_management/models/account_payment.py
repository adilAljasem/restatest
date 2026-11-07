from odoo import fields, models


class AccountPayment(models.Model):
    _inherit = "account.payment"

    employee_loan_id = fields.Many2one(
        "employee.loan",
        string="Employee Loan",
        copy=False,
        index=True,
        ondelete="restrict",
    )
    employee_loan_installment_id = fields.Many2one(
        "employee.loan.installment",
        string="Employee Loan Installment",
        copy=False,
        index=True,
        ondelete="restrict",
    )

    _employee_loan_unique = models.Constraint(
        "UNIQUE(employee_loan_id)",
        "A loan can only be disbursed once.",
    )
    _employee_loan_installment_unique = models.Constraint(
        "UNIQUE(employee_loan_installment_id)",
        "An installment can only be paid once.",
    )
