from odoo import api, fields, models, _
from odoo.exceptions import UserError


class EmployeeLoanPaymentWizard(models.TransientModel):
    _name = "employee.loan.payment.wizard"
    _description = "Pay Employee Loan"
    _check_company_auto = True

    loan_id = fields.Many2one(
        "employee.loan",
        required=True,
        readonly=True,
        check_company=True,
    )
    installment_id = fields.Many2one(
        "employee.loan.installment",
        readonly=True,
        check_company=True,
    )
    payment_kind = fields.Selection(
        [
            ("disbursement", "Loan Disbursement"),
            ("repayment", "Installment Repayment"),
        ],
        required=True,
        readonly=True,
    )
    payment_type = fields.Selection(
        [
            ("outbound", "Send"),
            ("inbound", "Receive"),
        ],
        compute="_compute_payment_values",
    )
    employee_id = fields.Many2one(
        related="loan_id.employee_id",
        readonly=True,
    )
    loan_reference = fields.Char(
        related="loan_id.name",
        readonly=True,
    )
    amount = fields.Monetary(
        string="Amount To Pay",
        compute="_compute_payment_values",
    )
    payment_date = fields.Date(
        default=fields.Date.context_today,
        required=True,
    )
    company_id = fields.Many2one(
        related="loan_id.company_id",
        readonly=True,
    )
    currency_id = fields.Many2one(
        related="loan_id.currency_id",
        readonly=True,
    )
    loan_account_id = fields.Many2one(
        related="company_id.employee_loan_account_id",
        string="Loan Account",
        readonly=True,
    )
    journal_id = fields.Many2one(
        "account.journal",
        required=True,
        check_company=True,
        domain="[('type', 'in', ('bank', 'cash')), ('company_id', '=', company_id)]",
    )
    payment_method_line_id = fields.Many2one(
        "account.payment.method.line",
        string="Payment Method",
        required=True,
        domain="[('journal_id', '=', journal_id), ('payment_type', '=', payment_type)]",
    )

    @api.depends("payment_kind", "loan_id.loan_amount", "installment_id.amount")
    def _compute_payment_values(self):
        for wizard in self:
            if wizard.payment_kind == "repayment":
                wizard.payment_type = "inbound"
                wizard.amount = wizard.installment_id.amount
            else:
                wizard.payment_type = "outbound"
                wizard.amount = wizard.loan_id.loan_amount

    @api.onchange("journal_id", "payment_kind")
    def _onchange_journal_id(self):
        for wizard in self:
            payment_methods = (
                wizard.journal_id.inbound_payment_method_line_ids
                if wizard.payment_kind == "repayment"
                else wizard.journal_id.outbound_payment_method_line_ids
            )
            if wizard.payment_method_line_id not in payment_methods:
                wizard.payment_method_line_id = payment_methods[:1]

    def action_create_payment(self):
        self.ensure_one()
        loan = self.loan_id
        installment = self.installment_id
        if loan.state != "approved":
            raise UserError(_("The loan must be Approved before creating a payment."))
        if not self.loan_account_id:
            raise UserError(
                _("Configure the Employee Loan Account in Accounting Settings first.")
            )
        if self.journal_id.type not in ("bank", "cash"):
            raise UserError(_("Select a Bank or Cash journal."))

        allowed_methods = (
            self.journal_id.inbound_payment_method_line_ids
            if self.payment_kind == "repayment"
            else self.journal_id.outbound_payment_method_line_ids
        )
        if self.payment_method_line_id not in allowed_methods:
            raise UserError(
                _("The selected Payment Method is not available for this journal.")
            )

        if self.payment_kind == "disbursement":
            if loan.disbursement_payment_id:
                raise UserError(_("This loan has already been paid to the employee."))
            payment_type = "outbound"
            partner_type = "supplier"
        else:
            if not installment or installment.loan_id != loan:
                raise UserError(_("Select a valid loan installment."))
            if not loan.is_disbursed:
                raise UserError(
                    _("Pay the loan to the employee before collecting installments.")
                )
            if installment.paid or installment.payment_id:
                raise UserError(_("This installment has already been paid."))
            if installment.payroll_state != "none" or installment.salary_attachment_id:
                raise UserError(
                    _("This installment is already being processed by Payroll.")
                )
            payment_type = "inbound"
            partner_type = "customer"

        partner = loan.employee_id.work_contact_id
        if not partner:
            raise UserError(
                _("The employee must have a Work Contact before creating a payment.")
            )

        payment_values = {
            "payment_type": payment_type,
            "partner_type": partner_type,
            "partner_id": partner.id,
            "amount": self.amount,
            "currency_id": self.currency_id.id,
            "date": self.payment_date,
            "journal_id": self.journal_id.id,
            "payment_method_line_id": self.payment_method_line_id.id,
            "destination_account_id": self.loan_account_id.id,
            "memo": (
                _("Loan %(loan)s", loan=loan.name)
                if self.payment_kind == "disbursement"
                else _(
                    "Loan %(loan)s - installment %(number)s",
                    loan=loan.name,
                    number=installment.sequence,
                )
            ),
        }
        if self.payment_kind == "disbursement":
            payment_values["employee_loan_id"] = loan.id
        else:
            payment_values["employee_loan_installment_id"] = installment.id

        payment = (
            self.env["account.payment"]
            .with_company(self.company_id)
            .create(payment_values)
        )
        payment.destination_account_id = self.loan_account_id
        payment.action_post()

        if self.payment_kind == "disbursement":
            loan.disbursement_payment_id = payment
        else:
            installment._mark_paid_direct(payment)

        # Keep the user on the loan form after either a disbursement or an
        # installment collection.  The reload refreshes the smart buttons,
        # totals, ribbon, and installment status without navigating to the
        # generated account.payment record.
        return {"type": "ir.actions.client", "tag": "reload"}
