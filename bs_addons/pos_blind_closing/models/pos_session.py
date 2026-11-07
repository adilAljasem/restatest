from markupsafe import Markup, escape

from odoo import _, models
from odoo.tools.misc import formatLang


class PosSession(models.Model):
    _inherit = "pos.session"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _is_blind_closing(self):
        """True when the current user must close this session blind."""
        self.ensure_one()
        config = self.config_id
        if not config.blind_closing:
            return False
        if config.blind_closing_manager_visible and self.env.user.has_group(
            "point_of_sale.group_pos_manager"
        ):
            return False
        return True

    def _blind_expected_bank_amounts(self):
        """Expected amount per bank payment method, computed exactly like
        the standard get_closing_control_data()."""
        self.ensure_one()
        orders = self._get_closed_orders()
        bank_methods = self.payment_method_ids.filtered(lambda pm: pm.type == "bank")
        return {
            pm.id: sum(orders.payment_ids.filtered(lambda p, pm=pm: p.payment_method_id == pm).mapped("amount"))
            for pm in bank_methods
        }

    # ------------------------------------------------------------------
    # Data sent to the closing popup
    # ------------------------------------------------------------------
    def get_closing_control_data(self):
        data = super().get_closing_control_data()
        blind = self._is_blind_closing()
        # False, or a dict of the options chosen in the POS settings
        data["blind_closing"] = self.config_id._get_blind_closing_options() if blind else False
        if not blind:
            return data

        # Never send the expected figures to the browser: hiding them in the
        # template only is not enough (they would be visible in the network tab).
        data["orders_details"] = {"quantity": 0, "amount": 0}
        cash = data.get("default_cash_details")
        if cash:
            cash["amount"] = 0
            cash["payment_amount"] = 0
            for move in cash.get("moves", []):
                move["amount"] = 0
        show_all_banks = self.config_id.blind_closing_show_all_banks
        for pm in data.get("non_cash_payment_methods", []):
            pm["amount"] = 0
            if show_all_banks:
                # don't reveal which methods were used
                pm["number"] = 0
        return data

    # ------------------------------------------------------------------
    # Closing
    # ------------------------------------------------------------------
    def close_session_from_ui(self, bank_payment_method_diff_pairs=None):
        """In blind mode the POS sends [(payment_method_id, counted_amount)]
        instead of [(payment_method_id, difference)]. The difference is
        computed here, on the server, from the real expected amounts."""
        self.ensure_one()
        if not self._is_blind_closing():
            return super().close_session_from_ui(bank_payment_method_diff_pairs)

        expected = self._blind_expected_bank_amounts()
        counted = {}
        for pm_id, amount in bank_payment_method_diff_pairs or []:
            if pm_id in expected:
                counted[pm_id] = float(amount or 0.0)
        # Any bank method the UI did not send is considered counted as 0
        for pm_id in expected:
            counted.setdefault(pm_id, 0.0)

        diff_pairs = [(pm_id, counted[pm_id] - expected[pm_id]) for pm_id in expected]
        result = super().close_session_from_ui(diff_pairs)
        if result.get("successful") and self.config_id.blind_closing_log:
            self._post_blind_closing_message(expected, counted)
        return result

    def _post_blind_closing_message(self, expected, counted):
        """Log expected vs counted on the session chatter (backend only)."""
        self.ensure_one()
        currency = self.currency_id
        fmt = lambda v: formatLang(self.env, v, currency_obj=currency)  # noqa: E731
        rows = []
        if self.config_id.cash_control and self.cash_journal_id:
            rows.append(
                (
                    _("Cash"),
                    self.cash_register_balance_end,
                    self.cash_register_balance_end_real,
                )
            )
        methods = self.env["pos.payment.method"].browse(list(expected))
        for pm in methods:
            rows.append((pm.name, expected[pm.id], counted[pm.id]))
        if not rows:
            return
        body = Markup("<b>%s</b><ul>") % _("Blind closing")
        for name, exp, cnt in rows:
            body += Markup("<li>%s: %s %s / %s %s / %s %s</li>") % (
                escape(name),
                _("Expected"), fmt(exp),
                _("Counted"), fmt(cnt),
                _("Difference"), fmt(cnt - exp),
            )
        body += Markup("</ul>")
        self.message_post(body=body)
