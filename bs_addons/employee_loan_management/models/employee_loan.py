from dateutil.relativedelta import relativedelta

from odoo import Command, api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import float_compare


class EmployeeLoan(models.Model):
    _name = "employee.loan"
    _description = "Employee Loan"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "request_date desc, id desc"
    _check_company_auto = True

    name = fields.Char(
        string="Loan Reference",
        required=True,
        readonly=True,
        copy=False,
        default=lambda self: _("New"),
        index="btree_not_null",
    )
    employee_id = fields.Many2one(
        "hr.employee",
        required=True,
        tracking=True,
        check_company=True,
        index="btree_not_null",
    )
    applicant_id = fields.Many2one(
        "res.users",
        string="Applicant",
        required=True,
        default=lambda self: self.env.user,
        tracking=True,
        index="btree_not_null",
    )
    department_id = fields.Many2one(
        "hr.department",
        related="employee_id.department_id",
        string="Department",
        store=True,
        readonly=True,
    )
    job_id = fields.Many2one(
        "hr.job",
        related="employee_id.job_id",
        string="Job Position",
        store=True,
        readonly=True,
    )
    request_date = fields.Date(
        default=fields.Date.context_today,
        required=True,
        tracking=True,
    )
    loan_amount = fields.Monetary(required=True, tracking=True)
    no_of_installments = fields.Integer(
        string="No. of Installments",
        required=True,
        default=1,
        tracking=True,
    )
    payment_start_date = fields.Date(required=True, tracking=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    currency_id = fields.Many2one(
        related="company_id.currency_id",
        readonly=True,
    )
    installment_ids = fields.One2many(
        "employee.loan.installment",
        "loan_id",
        string="Installments",
        copy=True,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("waiting", "Waiting Approve"),
            ("approved", "Approved"),
        ],
        default="draft",
        required=True,
        readonly=True,
        copy=False,
        tracking=True,
        index=True,
    )
    disbursement_payment_id = fields.Many2one(
        "account.payment",
        string="Loan Payment",
        readonly=True,
        copy=False,
        check_company=True,
    )
    is_disbursed = fields.Boolean(
        compute="_compute_is_disbursed",
        store=True,
    )
    total_paid_amount = fields.Monetary(
        compute="_compute_totals",
        store=True,
    )
    balance_amount = fields.Monetary(
        compute="_compute_totals",
        store=True,
    )
    installment_payment_count = fields.Integer(
        compute="_compute_installment_payment_count"
    )

    _positive_loan_amount = models.Constraint(
        "CHECK(loan_amount > 0)",
        "Loan Amount must be greater than zero.",
    )
    _positive_installment_count = models.Constraint(
        "CHECK(no_of_installments > 0)",
        "Number of Installments must be greater than zero.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            if not values.get("name") or values["name"] == _("New"):
                company = self.env["res.company"].browse(
                    values.get("company_id") or self.env.company.id
                )
                values["name"] = (
                    self.env["ir.sequence"]
                    .with_company(company)
                    .next_by_code("employee.loan")
                    or _("New")
                )
        return super().create(vals_list)

    def write(self, values):
        protected_fields = {
            "employee_id",
            "loan_amount",
            "no_of_installments",
            "payment_start_date",
            "installment_ids",
            "company_id",
        }
        if protected_fields.intersection(values) and any(
            loan.state != "draft" for loan in self
        ):
            raise UserError(
                _("Approved or submitted loans cannot have their core values changed.")
            )
        if "state" in values and not self.env.context.get("loan_workflow_update"):
            raise UserError(_("Use the workflow buttons to change the loan status."))
        return super().write(values)

    @api.depends("disbursement_payment_id", "disbursement_payment_id.state")
    def _compute_is_disbursed(self):
        for loan in self:
            loan.is_disbursed = bool(
                loan.disbursement_payment_id
                and loan.disbursement_payment_id.state not in ("draft", "canceled")
            )

    @api.depends("loan_amount", "installment_ids.amount", "installment_ids.paid")
    def _compute_totals(self):
        for loan in self:
            paid_amount = sum(
                loan.installment_ids.filtered("paid").mapped("amount")
            )
            loan.total_paid_amount = paid_amount
            loan.balance_amount = max(loan.loan_amount - paid_amount, 0.0)

    @api.depends("installment_ids.payment_id")
    def _compute_installment_payment_count(self):
        for loan in self:
            loan.installment_payment_count = len(
                loan.installment_ids.payment_id
            )

    @api.constrains("installment_ids", "loan_amount")
    def _check_paid_amount(self):
        for loan in self:
            paid_amount = sum(
                loan.installment_ids.filtered("paid").mapped("amount")
            )
            if float_compare(
                paid_amount,
                loan.loan_amount,
                precision_rounding=loan.currency_id.rounding,
            ) > 0:
                raise ValidationError(
                    _("Total Paid Amount cannot exceed the Loan Amount.")
                )

    @api.onchange("employee_id")
    def _onchange_employee_id(self):
        if self.employee_id.company_id:
            self.company_id = self.employee_id.company_id

    def action_compute_installments(self):
        for loan in self:
            if loan.state != "draft":
                raise UserError(_("Installments can only be computed in Draft."))
            if loan.installment_ids.filtered(
                lambda line: line.paid or line.payroll_state != "none"
            ):
                raise UserError(
                    _("Installments already collected or sent to payroll cannot be recomputed.")
                )
            if not loan.payment_start_date:
                raise UserError(_("Set a Payment Start Date first."))
            if loan.loan_amount <= 0 or loan.no_of_installments <= 0:
                raise UserError(
                    _("Loan Amount and Number of Installments must be greater than zero.")
                )

            regular_amount = loan.currency_id.round(
                loan.loan_amount / loan.no_of_installments
            )
            if float_compare(
                regular_amount,
                0.0,
                precision_rounding=loan.currency_id.rounding,
            ) <= 0:
                raise UserError(
                    _(
                        "The Loan Amount is too small for the selected number "
                        "of installments."
                    )
                )
            installment_commands = [Command.clear()]
            allocated = 0.0
            for installment_number in range(1, loan.no_of_installments + 1):
                amount = (
                    loan.loan_amount - allocated
                    if installment_number == loan.no_of_installments
                    else regular_amount
                )
                amount = loan.currency_id.round(amount)
                if float_compare(
                    amount,
                    0.0,
                    precision_rounding=loan.currency_id.rounding,
                ) <= 0:
                    raise UserError(
                        _(
                            "The Loan Amount is too small for the selected "
                            "number of installments."
                        )
                    )
                allocated += amount
                installment_commands.append(
                    Command.create(
                        {
                            "sequence": installment_number,
                            "payment_date": loan.payment_start_date
                            + relativedelta(months=installment_number - 1),
                            "amount": amount,
                        }
                    )
                )
            loan.installment_ids = installment_commands
        return True

    def action_send_to_approve(self):
        for loan in self:
            if loan.state != "draft":
                raise UserError(_("Only Draft loans can be submitted."))
            loan._validate_installment_schedule()
        self.with_context(loan_workflow_update=True).write({"state": "waiting"})
        return True

    def action_approve(self):
        if not (
            self.env.user.has_group(
                "employee_loan_management.group_employee_loan_approver"
            )
            or self.env.user.has_group("account.group_account_manager")
            or self.env.user.has_group("base.group_system")
        ):
            raise AccessError(_("You are not allowed to approve employee loans."))
        if any(loan.state != "waiting" for loan in self):
            raise UserError(_("Only loans waiting for approval can be approved."))
        self._validate_installment_schedule()
        self.with_context(loan_workflow_update=True).write({"state": "approved"})
        return True

    def _validate_installment_schedule(self):
        for loan in self:
            if len(loan.installment_ids) != loan.no_of_installments:
                raise UserError(
                    _("Compute the installment schedule before submitting the loan.")
                )
            total = sum(loan.installment_ids.mapped("amount"))
            if float_compare(
                total,
                loan.loan_amount,
                precision_rounding=loan.currency_id.rounding,
            ):
                raise UserError(
                    _("The installment total must equal the Loan Amount.")
                )

    def action_open_payment_wizard(self):
        self.ensure_one()
        if self.state != "approved":
            raise UserError(_("A loan must be Approved before it can be paid."))
        if self.disbursement_payment_id:
            raise UserError(_("This loan has already been paid to the employee."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Pay Loan"),
            "res_model": "employee.loan.payment.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_loan_id": self.id,
                "default_payment_kind": "disbursement",
            },
        }

    def action_view_disbursement_payment(self):
        self.ensure_one()
        return self.disbursement_payment_id.action_open_business_doc()

    def action_view_installment_payments(self):
        self.ensure_one()
        payments = self.installment_ids.payment_id
        action = self.env["ir.actions.actions"]._for_xml_id(
            "account.action_account_payments"
        )
        action["domain"] = [("id", "in", payments.ids)]
        return action

    @api.ondelete(at_uninstall=False)
    def _unlink_only_draft(self):
        if any(loan.state != "draft" for loan in self):
            raise UserError(_("Only Draft loans can be deleted."))


class EmployeeLoanInstallment(models.Model):
    _name = "employee.loan.installment"
    _description = "Employee Loan Installment"
    _order = "loan_id, sequence, payment_date"
    _check_company_auto = True

    loan_id = fields.Many2one(
        "employee.loan",
        required=True,
        ondelete="cascade",
        index="btree_not_null",
        check_company=True,
    )
    sequence = fields.Integer(required=True, default=1)
    payment_date = fields.Date(required=True, index=True)
    amount = fields.Monetary(required=True)
    paid = fields.Boolean(readonly=True, copy=False, index=True)
    salary_deduction = fields.Boolean()
    payroll_state = fields.Selection(
        [
            ("none", "Not Sent"),
            ("sent", "Sent to Payroll"),
            ("processed", "Deducted by Payroll"),
        ],
        default="none",
        required=True,
        readonly=True,
        copy=False,
        index=True,
    )
    payment_id = fields.Many2one(
        "account.payment",
        readonly=True,
        copy=False,
        check_company=True,
    )
    payroll_payslip_id = fields.Many2one(
        "resta.payslip",
        string="Payroll Payslip",
        readonly=True,
        copy=False,
    )
    employee_id = fields.Many2one(related="loan_id.employee_id", store=True)
    company_id = fields.Many2one(related="loan_id.company_id", store=True)
    currency_id = fields.Many2one(related="loan_id.currency_id")

    _positive_amount = models.Constraint(
        "CHECK(amount > 0)",
        "Installment Amount must be greater than zero.",
    )
    _loan_sequence_unique = models.Constraint(
        "UNIQUE(loan_id, sequence)",
        "Installment numbers must be unique per loan.",
    )

    def write(self, values):
        if {"amount", "payment_date", "loan_id"}.intersection(values) and any(
            line.loan_id.state != "draft" for line in self
        ):
            raise UserError(_("Approved installments cannot be changed."))
        if "salary_deduction" in values and any(
            line.paid or line.payroll_state != "none" for line in self
        ):
            raise UserError(
                _("Collected installments cannot change their deduction method.")
            )
        return super().write(values)

    def action_open_payment_wizard(self):
        self.ensure_one()
        if self.loan_id.state != "approved":
            raise UserError(_("The loan must be Approved first."))
        if not self.loan_id.is_disbursed:
            raise UserError(_("Pay the loan to the employee before collecting installments."))
        if self.paid or self.payment_id:
            raise UserError(_("This installment has already been paid."))
        if self.payroll_state != "none":
            raise UserError(_("This installment is already being processed by Payroll."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Pay Installment"),
            "res_model": "employee.loan.payment.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_loan_id": self.loan_id.id,
                "default_installment_id": self.id,
                "default_payment_kind": "repayment",
            },
        }

    def _mark_paid_direct(self, payment):
        self.ensure_one()
        if self.paid or self.payroll_state != "none":
            raise UserError(_("This installment has already been collected."))
        projected_total = self.loan_id.total_paid_amount + self.amount
        if float_compare(
            projected_total,
            self.loan_id.loan_amount,
            precision_rounding=self.currency_id.rounding,
        ) > 0:
            raise UserError(_("Total Paid Amount cannot exceed the Loan Amount."))
        self.write({"paid": True, "payment_id": payment.id})

    def _mark_paid_from_payroll(self, payslip):
        self.ensure_one()
        if self.paid:
            return
        if self.payroll_state != "sent" or self.payroll_payslip_id != payslip:
            raise UserError(_("This installment was not sent to Payroll."))
        projected_total = self.loan_id.total_paid_amount + self.amount
        if float_compare(
            projected_total,
            self.loan_id.loan_amount,
            precision_rounding=self.currency_id.rounding,
        ) > 0:
            raise UserError(_("Total Paid Amount cannot exceed the Loan Amount."))
        self.write({"paid": True, "payroll_state": "processed"})

    @api.model
    def _due_for_payslip(self, payslip):
        """Installments the payslip must deduct: due by the end of its period, not collected yet."""
        return self.search([
            ("employee_id", "=", payslip.employee_id.id),
            ("salary_deduction", "=", True),
            ("paid", "=", False),
            ("payment_date", "<=", payslip.date_to),
            ("loan_id.state", "=", "approved"),
            ("loan_id.is_disbursed", "=", True),
            "|", ("payroll_state", "=", "none"),
            "&", ("payroll_state", "=", "sent"), ("payroll_payslip_id", "=", payslip.id),
        ], order="payment_date, sequence")
