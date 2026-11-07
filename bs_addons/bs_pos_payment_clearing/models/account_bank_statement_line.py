from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class AccountBankStatementLine(models.Model):
    _inherit = "account.bank.statement.line"

    bs_source_cash_journal_id = fields.Many2one(
        "account.journal", readonly=True, copy=False, check_company=True, ondelete="restrict",
    )
    bs_cash_outstanding_account_id = fields.Many2one(
        "account.account", readonly=True, copy=False, check_company=True, ondelete="restrict",
    )
    bs_cash_counterpart_account_id = fields.Many2one(
        "account.account", readonly=True, copy=False, check_company=True, ondelete="restrict",
    )

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [dict(vals) for vals in vals_list]
        for vals in vals_list:
            session = self.env["pos.session"].browse(vals.get("pos_session_id"))
            if not session.bs_clearing_journal_id:
                continue
            session._bs_check_clearing_configuration()
            journal = self.env["account.journal"].browse(vals.get("journal_id"))
            method = session.payment_method_ids.filtered(lambda method: method.journal_id == journal and method.is_cash_count)
            if len(method) != 1:
                raise ValidationError(_("A POS cash movement must use a cash payment method of its session."))
            if vals.get("statement_id"):
                raise ValidationError(_("POS clearing cash movements cannot be attached to a bank statement."))
            if "line_ids" in vals:
                raise ValidationError(_("POS cash journal items are generated automatically from the cash movement."))
            if vals.get("foreign_currency_id"):
                if vals["foreign_currency_id"] != session.currency_id.id:
                    raise ValidationError(_("POS cash movements must be expressed in the session currency."))
                vals["amount"] = vals.get("amount_currency", 0)
            vals.update(
                journal_id=session.bs_clearing_journal_id.id,
                bs_pos_clearing_session_id=session.id,
                bs_pos_payment_journal_id=journal.id,
                bs_pos_outstanding_account_id=method.outstanding_account_id.id,
                bs_source_cash_journal_id=journal.id,
                bs_cash_outstanding_account_id=method.outstanding_account_id.id,
                bs_cash_counterpart_account_id=vals.get("counterpart_account_id") or journal.suspense_account_id.id,
                foreign_currency_id=False,
                amount_currency=0,
            )
        lines = super().create(vals_list)
        # Optional compatibility with puls-sa/pos_analytic_account. That addon's
        # cash-difference search assumes the move is in the source cash journal.
        for line in lines.filtered("bs_source_cash_journal_id"):
            config = line.pos_session_id.config_id
            if "analytic_account_id" in config._fields and hasattr(line.move_id.line_ids, "_apply_pos_analytic_account"):
                line.move_id.line_ids._apply_pos_analytic_account(config.analytic_account_id)
        return lines

    @api.constrains(
        "bs_source_cash_journal_id", "bs_cash_outstanding_account_id",
        "bs_cash_counterpart_account_id", "pos_session_id", "journal_id",
    )
    def _bs_check_cash_routing(self):
        for line in self.filtered("bs_source_cash_journal_id"):
            if (
                not line.pos_session_id.bs_clearing_journal_id
                or line.bs_pos_clearing_session_id != line.pos_session_id
                or line.journal_id != line.pos_session_id.bs_clearing_journal_id
                or line.bs_source_cash_journal_id.type != "cash"
                or not line.bs_cash_outstanding_account_id
                or not line.bs_cash_counterpart_account_id
                or line.bs_cash_outstanding_account_id == line.bs_cash_counterpart_account_id
            ):
                raise ValidationError(_("Invalid POS cash clearing configuration."))
            line._check_company([
                "bs_source_cash_journal_id", "bs_cash_outstanding_account_id", "bs_cash_counterpart_account_id",
            ])

    def _prepare_move_line_default_vals(self, counterpart_account_id=None):
        if not self.bs_source_cash_journal_id:
            return super()._prepare_move_line_default_vals(counterpart_account_id)
        values = super()._prepare_move_line_default_vals(
            counterpart_account_id or self.bs_cash_counterpart_account_id.id,
        )
        values[0]["account_id"] = self.bs_cash_outstanding_account_id.id
        return values

    def _seek_for_lines(self):
        if not self.bs_source_cash_journal_id:
            return super()._seek_for_lines()
        self.ensure_one()
        lines = self.move_id.line_ids
        liquidity = lines.filtered(lambda line: line.account_id == self.bs_cash_outstanding_account_id)
        suspense = (lines - liquidity).filtered(
            lambda line: line.account_id == self.bs_source_cash_journal_id.suspense_account_id,
        )
        return liquidity, suspense, lines - liquidity - suspense

    def write(self, vals):
        protected = {
            "bs_source_cash_journal_id", "bs_cash_outstanding_account_id",
            "bs_cash_counterpart_account_id", "pos_session_id", "journal_id", "statement_id",
        }
        for line in self.filtered("bs_source_cash_journal_id"):
            if any(line[field].id != vals[field] for field in protected & vals.keys()):
                raise ValidationError(_("The routing of an existing POS cash movement cannot be changed."))
        return super().write(vals)
