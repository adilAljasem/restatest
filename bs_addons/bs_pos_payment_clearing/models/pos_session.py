from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError


class PosSession(models.Model):
    _inherit = "pos.session"

    def _bs_opening_date(self):
        self.ensure_one()
        return fields.Date.context_today(self, timestamp=self.start_at)

    bs_clearing_journal_id = fields.Many2one(
        "account.journal", string="Payment Clearing Journal", readonly=True,
        copy=False, check_company=True, ondelete="restrict",
        help="Journal captured when a new session starts. Empty on sessions predating this module.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [dict(vals) for vals in vals_list]
        for vals in vals_list:
            config = self.env["pos.config"].browse(vals.get("config_id") or self.env.context.get("default_config_id"))
            if config:
                config.check_access("read")
                journal = config.journal_id
                if not journal or not journal.active or journal.type not in ("general", "sale"):
                    raise UserError(_("Select an active Point of Sale Journal in the POS settings before opening a session."))
                methods = config.payment_method_ids
                methods.check_access("read")
                methods._bs_check_outstanding_account()
                config._check_company(["journal_id"])
                vals["bs_clearing_journal_id"] = journal.id
        return super().create(vals_list)

    def write(self, vals):
        if "config_id" in vals and any(
            session.bs_clearing_journal_id and session.config_id.id != vals["config_id"] for session in self
        ):
            raise UserError(_("The point of sale cannot be changed on an existing clearing session."))
        if "bs_clearing_journal_id" in vals and any(
            session.bs_clearing_journal_id.id != vals["bs_clearing_journal_id"] for session in self
        ):
            raise UserError(_("The payment clearing journal cannot be changed on an existing POS session."))
        result = super().write(vals)
        if "closing_notes" in vals:
            for session in self:
                for component in session.bs_collection_component_ids.filtered(
                    lambda item: item.journal_id.type == "bank"
                    and item.move_id.state in ("draft", "posted"),
                ):
                    label = session.closing_notes or component.name
                    component.line_ids.filtered(
                        lambda line: line.name != label,
                    ).write({"name": label})
            sources = self.env["account.move"].search([
                ("bs_pos_clearing_session_id", "in", self.ids),
                ("state", "=", "posted"),
            ])
            sources._bs_sync_pos_settlements()
        return result

    def _bs_check_clearing_configuration(self):
        for session in self.filtered("bs_clearing_journal_id"):
            session.check_access("read")
            if not session.bs_clearing_journal_id.active:
                raise UserError(_("The payment clearing journal of session %s is archived.", session.display_name))
            session._check_company(["bs_clearing_journal_id"])
            methods = session.payment_method_ids
            methods.check_access("read")
            methods._bs_check_outstanding_account()

    def _validate_session(self, balancing_account=False, amount_to_balance=0, bank_payment_method_diffs=None):
        for session in self.filtered(lambda session: session.bs_clearing_journal_id and not session.config_id.cash_control):
            # Without a cash count, use the theoretical amount. Otherwise the
            # untouched zero count would be treated as a fictitious cash loss.
            session.cash_register_balance_end_real = session.cash_register_balance_end
            session.invalidate_recordset(["cash_register_balance_end", "cash_register_difference"])
        return super()._validate_session(balancing_account, amount_to_balance, bank_payment_method_diffs)

    def _create_account_move(self, balancing_account=False, amount_to_balance=0, bank_payment_method_diffs=None):
        self._bs_check_clearing_configuration()
        opening_date = self._bs_opening_date()
        data = super(PosSession, self.with_context(default_date=opening_date))._create_account_move(
            balancing_account, amount_to_balance, bank_payment_method_diffs,
        )
        self.move_id.date = opening_date
        return data

    def _get_stock_expense_vals(self, exp_account, amount, amount_converted):
        orders = self._get_closed_orders()
        fiscal_positions = orders.mapped("fiscal_position_id")
        if len(fiscal_positions) == 1 and all(
            order.fiscal_position_id == fiscal_positions for order in orders
        ):
            exp_account = fiscal_positions.map_account(exp_account)
        return super()._get_stock_expense_vals(exp_account, amount, amount_converted)

    def _get_combine_statement_line_vals(self, journal, amount, payment_method):
        vals = super()._get_combine_statement_line_vals(journal, amount, payment_method)
        vals["date"] = self._bs_opening_date()
        if self.bs_clearing_journal_id:
            vals.update(amount=amount, foreign_currency_id=False, amount_currency=0)
        return vals

    def _get_split_statement_line_vals(self, journal, amount, payment):
        vals = super()._get_split_statement_line_vals(journal, amount, payment)
        vals["date"] = self._bs_opening_date()
        if self.bs_clearing_journal_id:
            vals.update(amount=amount, foreign_currency_id=False, amount_currency=0)
        return vals

    def _ensure_payment_outstanding_account(self, payment, payment_amount):
        if self.bs_clearing_journal_id and self.currency_id.compare_amounts(payment_amount, 0) < 0:
            # Outbound already reverses debit/credit. Swapping the two accounts
            # as well would debit Outstanding again instead of refunding it.
            payment.payment_type = "outbound"
            return None
        return super()._ensure_payment_outstanding_account(payment, payment_amount)

    def _apply_diff_on_account_payment_move(self, account_payment, payment_method, diff_amount):
        if not self.bs_clearing_journal_id:
            return super()._apply_diff_on_account_payment_move(account_payment, payment_method, diff_amount)
        values = self._get_diff_vals(payment_method.id, diff_amount, account_payment.outstanding_account_id)
        if not values:
            return None
        source, destination = values
        move = account_payment.move_id
        outstanding = move.line_ids.filtered(lambda line: line.account_id.id == source["account_id"])
        new_balance = outstanding.balance + source.get("debit", 0) - source.get("credit", 0)
        new_amount_currency = outstanding.amount_currency + source.get("amount_currency", diff_amount)
        move.button_draft()
        move.write({"line_ids": [
            Command.create(destination),
            Command.update(outstanding.id, {"balance": new_balance, "amount_currency": new_amount_currency}),
        ]})
        # The payment amount is in its transaction currency, not company currency.
        # Keep native payment/move synchronization and its write-off handling.
        account_payment.write({
            "amount": abs(new_amount_currency),
            "payment_type": "outbound" if new_amount_currency < 0 else "inbound",
        })
        move.action_post()
        return None

    def _post_statement_difference(self, amount):
        existing_lines = self.statement_line_ids
        result = super()._post_statement_difference(amount)
        new_lines = self.statement_line_ids - existing_lines
        if new_lines:
            new_lines.write({"date": self._bs_opening_date()})
        return result

    def _create_diff_account_move_for_split_payment_method(self, payment_method, diff_amount):
        if not self.bs_clearing_journal_id:
            return super()._create_diff_account_move_for_split_payment_method(payment_method, diff_amount)
        self.ensure_one()
        self._bs_check_clearing_configuration()
        values = self._get_diff_vals(payment_method.id, diff_amount)
        if values:
            self.env["account.move"].create({
                "journal_id": self.bs_clearing_journal_id.id,
                "bs_pos_clearing_session_id": self.id,
                "bs_pos_payment_journal_id": payment_method.journal_id.id,
                "bs_pos_outstanding_account_id": payment_method.outstanding_account_id.id,
                "date": self._bs_opening_date(),
                "ref": self._get_diff_account_move_ref(payment_method),
                "line_ids": [Command.create(value) for value in values],
            })._post()
        return None
