from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class PosPaymentMethod(models.Model):
    _inherit = "pos.payment.method"

    partner_id = fields.Many2one(
        "res.partner", string="Customer", check_company=True, ondelete="restrict",
        help="Customer selected automatically in the POS when this payment method identifies customers.",
    )

    @api.constrains("split_transactions", "partner_id", "journal_id")
    def _bs_check_split_payment_partner(self):
        for method in self.filtered(
            lambda method: method.type in ("cash", "bank")
            and method.split_transactions and not method.partner_id,
        ):
            raise ValidationError(_("Select a Customer for payment method %s.", method.display_name))

    @api.model
    def _load_pos_data_fields(self, config):
        return super()._load_pos_data_fields(config) + ["partner_id"]

    bs_branch_cash_account_id = fields.Many2one(
        "account.account", string="Branch Cash Account", check_company=True, ondelete="restrict",
        domain="[('account_type', '=', 'asset_cash')]",
        help="For new sessions, collect cash in the cash journal's default drawer account and "
             "create a draft transfer of the net collection to this separate branch cash account. "
             "Leave empty to keep the Outstanding Account workflow.",
    )

    @api.constrains("bs_branch_cash_account_id", "journal_id", "company_id")
    def _bs_check_branch_cash_account(self):
        for method in self.filtered("bs_branch_cash_account_id"):
            drawer = method.journal_id.default_account_id
            branch = method.bs_branch_cash_account_id
            if (
                method.journal_id.type != "cash" or not drawer or not drawer.active
                or drawer.account_type != "asset_cash" or not branch.active
                or branch.account_type != "asset_cash" or branch == drawer
            ):
                raise ValidationError(_("Select distinct, active cash accounts for the POS drawer and branch cash box."))
            method._check_company(["journal_id", "bs_branch_cash_account_id"])

    outstanding_account_id = fields.Many2one(
        check_company=True,
        domain="[('reconcile', '=', True), ('account_type', '=', 'asset_current')]",
        help="Required clearing account for cash and bank POS collections. "
             "Amounts remain here until the corresponding settlement is reconciled.",
    )

    @api.model
    def _bs_default_outstanding_account(self, company, journal):
        accounts = journal.inbound_payment_method_line_ids.payment_account_id
        account = accounts.filtered(
            lambda account: account.account_type == "asset_current" and account.reconcile
            and account != journal.suspense_account_id,
        )[:1]
        if not account:
            chart = self.with_company(company).env["account.chart.template"]
            account = chart.ref("account_journal_payment_debit_account_id", raise_if_not_found=False)
        return account or company.transfer_account_id

    @api.model_create_multi
    def create(self, vals_list):
        # Native POS provisioning creates methods without calling onchanges.
        # An explicitly empty account is never silently replaced.
        vals_list = [dict(vals) for vals in vals_list]
        for vals in vals_list:
            journal = self.env["account.journal"].browse(vals.get("journal_id"))
            if journal.type in ("cash", "bank") and "outstanding_account_id" not in vals:
                company = self.env["res.company"].browse(vals.get("company_id")) or self.env.company
                vals["outstanding_account_id"] = self._bs_default_outstanding_account(company, journal).id
        return super().create(vals_list)

    @api.onchange("journal_id")
    def _onchange_journal_id(self):
        previous = {method: method.outstanding_account_id for method in self}
        super()._onchange_journal_id()
        for method in self:
            if method.journal_id.type in ("cash", "bank"):
                method.outstanding_account_id = previous[method] or method._bs_default_outstanding_account(
                    method.company_id, method.journal_id,
                )

    @api.constrains("journal_id", "outstanding_account_id", "company_id", "receivable_account_id")
    def _bs_check_outstanding_account(self):
        for method in self.filtered(lambda method: method.journal_id.type in ("cash", "bank")):
            account = method.outstanding_account_id
            if not account:
                raise ValidationError(_("Select an Outstanding Account for payment method %s.", method.display_name))
            if not account.active or account.account_type != "asset_current" or not account.reconcile:
                raise ValidationError(_("The Outstanding Account must be a reconcilable Current Asset account."))
            if account in (
                method.journal_id.default_account_id | method.journal_id.suspense_account_id
                | method.receivable_account_id | method.company_id.account_default_pos_receivable_account_id
            ):
                raise ValidationError(_("The Outstanding Account must differ from the cash/bank, suspense and POS receivable accounts."))
            method._check_company(["journal_id", "outstanding_account_id"])
