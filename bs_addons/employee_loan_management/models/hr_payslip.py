from odoo import models


class HrPayslip(models.Model):
    _inherit = "hr.payslip"

    def action_payslip_done(self):
        result = super().action_payslip_done()
        for payslip in self.filtered(
            lambda slip: slip.state == "validated" and not slip.credit_note
        ):
            attachments = payslip.salary_attachment_ids.filtered(
                "employee_loan_installment_id"
            )
            for attachment in attachments:
                attachment.employee_loan_installment_id._mark_paid_from_payroll(
                    payslip
                )
        return result
