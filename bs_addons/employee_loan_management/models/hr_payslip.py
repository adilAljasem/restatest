from odoo import _, models
from odoo.exceptions import UserError

SOURCE = "employee.loan.installment:%d"


class RestaPayslip(models.Model):
    """Loan installments marked "salary deduction" are deducted by the Resta payslip of the month."""
    _inherit = "resta.payslip"

    def _resta_collect_inputs(self):
        res = super()._resta_collect_inputs()
        Installment = self.env["employee.loan.installment"].sudo()
        for slip in self:
            company = slip.company_id
            input_type = company.employee_loan_salary_input_type_id or \
                self.env.ref("employee_loan_management.input_loan", raise_if_not_found=False)
            due = Installment._due_for_payslip(slip)
            if due and not input_type:
                raise UserError(_("Configure the Loan Salary Input Type in Accounting Settings first."))
            existing = {i.source_ref for i in slip.input_ids if i.source_ref}
            for installment in due:
                ref = SOURCE % installment.id
                if ref not in existing:
                    slip.input_ids = [(0, 0, {
                        "type_id": input_type.id, "amount": installment.amount, "source_ref": ref,
                        "account_id": company.employee_loan_account_id.id,
                        "name": _("Loan %(loan)s - installment %(number)s",
                                  loan=installment.loan_id.name, number=installment.sequence),
                    })]
            due.write({"payroll_state": "sent", "payroll_payslip_id": slip.id})
        return res

    def _loan_installments(self):
        ids = [int(i.source_ref.split(":")[1]) for i in self.input_ids
               if i.source_ref and i.source_ref.startswith("employee.loan.installment:")]
        return self.env["employee.loan.installment"].sudo().browse(ids).exists()

    def _resta_on_confirm(self):
        res = super()._resta_on_confirm()
        for slip in self:
            for installment in slip._loan_installments():
                installment._mark_paid_from_payroll(slip)
        return res

    def _resta_on_cancel(self):
        res = super()._resta_on_cancel()
        for slip in self:
            slip._loan_installments().filtered(lambda i: i.payroll_payslip_id == slip).write(
                {"paid": False, "payroll_state": "none", "payroll_payslip_id": False})
        return res
