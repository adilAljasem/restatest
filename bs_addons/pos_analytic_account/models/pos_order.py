from odoo import api, fields, models


class PosOrder(models.Model):
    _inherit = "pos.order"

    pos_analytic_account_id = fields.Many2one(
        comodel_name="account.analytic.account",
        string="POS Analytic Account",
        check_company=True,
        copy=True,
        readonly=True,
        index=True,
        help="Analytic account captured from the Point of Sale when the order was created.",
    )

    def _complete_values_from_session(self, session, values):
        values = super()._complete_values_from_session(session, values)
        values.setdefault(
            "pos_analytic_account_id",
            session.config_id.analytic_account_id.id or False,
        )
        return values

    @api.model_create_multi
    def create(self, vals_list):
        orders = super().create(vals_list)
        for order in orders.filtered("refunded_order_id"):
            original_account = order.refunded_order_id.pos_analytic_account_id
            if original_account != order.pos_analytic_account_id:
                order.sudo().pos_analytic_account_id = original_account
        return orders

    def _get_pos_analytic_account(self):
        self.ensure_one()
        return (
            self.refunded_order_id.pos_analytic_account_id
            or self.pos_analytic_account_id
        )

    def _create_invoice(self, move_vals):
        sessions = self.mapped("session_id")
        if len(sessions) == 1 and sessions.start_at:
            move_vals["date"] = fields.Datetime.context_timestamp(
                self, sessions.start_at
            ).date()
        invoice = super()._create_invoice(move_vals)
        analytic_accounts = self.mapped("pos_analytic_account_id")
        if len(analytic_accounts) == 1:
            invoice.invoice_line_ids._apply_pos_analytic_account(analytic_accounts)
        elif len(self) == 1:
            invoice.invoice_line_ids._apply_pos_analytic_account(
                self._get_pos_analytic_account()
            )
        return invoice

    def _create_misc_reversal_move(self, payment_moves):
        previous_reversal_moves = self.reversed_move_ids
        result = super()._create_misc_reversal_move(payment_moves)
        new_reversal_moves = self.reversed_move_ids - previous_reversal_moves
        new_reversal_moves.line_ids._apply_pos_analytic_account(
            self._get_pos_analytic_account()
        )
        return result
