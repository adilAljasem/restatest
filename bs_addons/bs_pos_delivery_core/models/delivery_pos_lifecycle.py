import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .delivery_order_workflow import DeliveryPosSessionUnavailable

_logger = logging.getLogger(__name__)


class DeliveryPosLifecycle(models.Model):
    _inherit = "bs.delivery.order"

    pos_processing_state = fields.Selection(
        [("pending", "Pending"), ("waiting_session", "Awaiting POS Session"),
         ("ready", "POS Created"),
         ("done", "Completed"), ("blocked", "Needs Review")],
        default="pending", required=True, readonly=True, copy=False, index=True,
    )
    pos_processing_error = fields.Text(readonly=True, copy=False)
    session_wait_started_at = fields.Datetime(readonly=True, copy=False)
    acceptance_deadline_at = fields.Datetime(readonly=True, copy=False, index=True)
    session_activity_id = fields.Many2one("mail.activity", readonly=True, copy=False)
    pos_refund_order_id = fields.Many2one(
        "pos.order", readonly=True, copy=False, check_company=True, ondelete="restrict",
    )

    def _set_pos_processing_result(self, state, error=False):
        self.sudo()._with_delivery_internal_update().write(
            {"pos_processing_state": state, "pos_processing_error": error or False},
        )
        if state != "blocked" or not error:
            return
        for order in self:
            try:
                with self.env.cr.savepoint():
                    log = self.env["bs.delivery.sync.log"].start_attempt(
                        order.connection_id,
                        direction="incoming",
                        operation="core:pos_processing",
                        binding=order.binding_id or None,
                        webhook_event=order.webhook_event_id or None,
                        external_id=order.external_order_id,
                        request_payload={"external_order_id": order.external_order_id},
                    )
                    log.mark_failed(
                        str(error),
                        response_payload={"pos_processing_state": "blocked"},
                    )
            except Exception:
                _logger.exception("Could not audit blocked POS processing for order %s", order.id)

    def _before_delivery_state_transition(self, new_state, source):
        super()._before_delivery_state_transition(new_state, source)
        if (new_state in ("preparing", "ready", "picked_up", "delivered")
                and source not in ("provider", "webhook")
                and self.pos_processing_state in ("waiting_session", "blocked")):
            raise UserError(_("Complete POS accounting before advancing this delivery order."))
        if new_state == "accepted":
            try:
                self.create_pos_order()
            except DeliveryPosSessionUnavailable:
                arrived_at = (
                    self.webhook_event_id.create_date or self.create_date or fields.Datetime.now()
                )
                timeout_minutes = self.connection_id.closed_pos_retry_timeout_minutes or 15
                deadline = arrived_at + timedelta(minutes=timeout_minutes)
                if source not in ("provider", "webhook") and fields.Datetime.now() >= deadline:
                    raise UserError(_("The aggregator acceptance window has expired.")) from None
                self.sudo()._with_delivery_internal_update().write({
                    "session_wait_started_at": arrived_at,
                    "acceptance_deadline_at": deadline,
                })
                self._set_pos_processing_result("waiting_session")
                self.message_post(body=_(
                    "Awaiting POS Session: accepted order will be settled when the POS opens (wait timeout: %s minutes)."
                ) % timeout_minutes)
            else:
                self._finalize_accepted_pos_order()

    def _resume_awaiting_pos_session(self, *, retry_blocked=False):
        """Settle a remotely accepted order exactly once after its POS opens."""
        for order in self.sudo():
            order = order.with_company(order.company_id)
            self.env.cr.execute(
                "SELECT id FROM bs_delivery_order WHERE id = %s FOR UPDATE", [order.id],
            )
            order.invalidate_recordset()
            allowed_states = ("waiting_session", "blocked") if retry_blocked else ("waiting_session",)
            if order.pos_processing_state not in allowed_states or order.state not in (
                "accepted", "preparing", "ready", "picked_up",
            ):
                continue
            if not order.store_binding_id or not order.store_binding_id.pos_config_id:
                continue
            session = order.store_binding_id.pos_config_id.current_session_id
            if not session or session.state != "opened":
                if retry_blocked:
                    raise UserError(_("Open the mapped POS session before retrying POS accounting."))
                continue
            accepted_transition = order.transition_ids.filtered(
                lambda transition: transition.to_state == "accepted"
            )[:1]
            if (not accepted_transition
                    or accepted_transition.source not in ("provider", "webhook")):
                acceptance_job = self.env["bs.delivery.outbox"].sudo().search([
                    ("binding_id", "=", order.binding_id.id),
                    "|", ("operation", "=", "accept_order"),
                    "&", ("operation", "=", "update_order"),
                    ("status_value", "=", "accepted"),
                ], order="id desc", limit=1)
                if not acceptance_job or acceptance_job.state != "done":
                    if retry_blocked:
                        raise UserError(_(
                            "Confirm that the aggregator accepted this order before retrying POS accounting."
                        ))
                    continue
            try:
                with self.env.cr.savepoint():
                    order.create_pos_order()
                    order._finalize_accepted_pos_order()
            except UserError as error:
                order._set_pos_processing_result("blocked", str(error))
                order.message_post(body=_("POS accounting needs review: %s") % str(error))
            else:
                unresolved_jobs = self.env["bs.delivery.outbox"].sudo().search_count([
                    ("binding_id", "=", order.binding_id.id),
                    ("state", "not in", ["done", "cancelled"]),
                ], limit=1)
                if not unresolved_jobs and accepted_transition.source not in ("provider", "webhook"):
                    order.binding_id.sudo().mark_sync_success()
                order._request_kitchen_autoprint()
                order._finish_session_activity()
            order._notify_pos_board("state_changed")
        return True

    @api.model
    def _cron_resume_awaiting_pos_sessions(self, batch_size=50):
        config_ids = self.env["pos.session"].sudo().search([
            ("state", "=", "opened"),
        ]).mapped("config_id").ids
        resumed_count = 0
        if config_ids:
            orders = self.sudo().search([
                ("pos_processing_state", "=", "waiting_session"),
                ("state", "in", ["accepted", "preparing", "ready", "picked_up"]),
                ("store_binding_id.pos_config_id", "in", config_ids),
            ], order="received_at, id", limit=batch_size)
            orders._resume_awaiting_pos_session()
            resumed_count = len(orders)

        # Check orders where closed POS wait deadline has expired
        now = fields.Datetime.now()
        expired_orders = self.sudo().search([
            ("pos_processing_state", "=", "waiting_session"),
            ("state", "in", ["accepted", "preparing", "ready", "picked_up"]),
            ("acceptance_deadline_at", "!=", False),
            ("acceptance_deadline_at", "<=", now),
        ], limit=batch_size)
        for expired_order in expired_orders:
            conn = expired_order.connection_id
            if conn.closed_pos_auto_reject_on_timeout:
                try:
                    with self.env.cr.savepoint():
                        expired_order.transition_to(
                            "rejected",
                            source="automatic",
                            reason=_("POS session was not opened within the allowed wait window."),
                            reject_reason_id=conn.delivery_auto_reject_reason_id.id or False,
                            idempotency_key="closed-pos-timeout:%s" % expired_order.id,
                        )
                except Exception as error:
                    expired_order._set_pos_processing_result(
                        "blocked",
                        _("POS wait expired but auto-rejection failed: %s") % str(error),
                    )
            else:
                expired_order._set_pos_processing_result(
                    "blocked",
                    _("POS session was not opened within the allowed %s-minute wait deadline.")
                    % (conn.closed_pos_retry_timeout_minutes or 15),
                )
        return resumed_count

    def _finalize_accepted_pos_order(self):
        """Complete payment and invoice before the delivery state can advance."""
        self.ensure_one()
        pos_order = self.pos_order_id
        if not pos_order or pos_order.state != "draft":
            raise UserError(_("A draft POS order is required before accepting the delivery order."))
        if not pos_order.partner_id:
            raise UserError(_("Choose an invoice customer before accepting the delivery order."))
        journal = pos_order.config_id.invoice_journal_id
        if not journal or journal.type != "sale" or journal.company_id != self.company_id:
            raise UserError(_("Configure a sales invoice journal on the POS branch."))
        if pos_order.session_id.state not in ("opening_control", "opened"):
            raise UserError(_("The POS session must be open to invoice the delivery order."))
        if self.currency_id.compare_amounts(pos_order.amount_paid, pos_order.amount_total):
            raise UserError(_("The delivery POS order must be fully paid before acceptance."))
        pos_order._compute_prices()
        if (self._compare_provider_financial_amounts(self.amount_total, pos_order.amount_total)
                or self._compare_provider_financial_amounts(self.vat_amount, pos_order.amount_tax)):
            raise UserError(_("POS totals or taxes changed before delivery acceptance."))
        pos_order.with_context(bs_delivery_core_import=True, generate_pdf=False).write({"to_invoice": True})
        pos_order.with_context(bs_delivery_core_import=True, generate_pdf=False)._process_saved_order(False)
        invoice = pos_order.account_move
        if (pos_order.state != "done" or not invoice or invoice.state != "posted"
                or self._compare_provider_financial_amounts(self.amount_total, invoice.amount_total)
                or self._compare_provider_financial_amounts(self.vat_amount, invoice.amount_tax)
                or not self.currency_id.is_zero(invoice.amount_residual)):
            raise UserError(_("The delivery order was not fully paid and invoiced."))
        self._set_pos_processing_result("done")
        try:
            with self.env.cr.savepoint():
                self._request_cashier_receipt_autoprint()
        except Exception:
            # A printer notification must never undo a paid POS order or invoice.
            _logger.exception("Could not queue cashier receipt for delivery order %s", self.id)

    def _create_pos_order_hook(self):
        order = super()._create_pos_order_hook()
        self._set_pos_processing_result("ready")
        return order

    def _after_delivery_state_transition(self, transition):
        result = super()._after_delivery_state_transition(transition)
        if (not transition.is_noop and transition.to_state == "accepted"
                and self.pos_processing_state == "waiting_session"):
            self._alert_delivery_manager_for_session()
        if not transition.is_noop and transition.to_state in (
            "delivered", "cancelled", "rejected",
        ):
            self._try_sync_terminal_pos_order()
            if transition.to_state in ("cancelled", "rejected"):
                self._finish_session_activity()
        return result

    def _alert_delivery_manager_for_session(self):
        for order in self:
            group = self.env.ref("bs_pos_delivery_core.group_delivery_manager")
            managers = group.sudo().all_user_ids.filtered(
                lambda user: user.active and order.company_id in user.company_ids
            )
            if managers:
                activity = order.sudo().activity_schedule(
                    "mail.mail_activity_data_warning",
                    user_id=managers.sorted("id")[0].id,
                    summary=_("Aggregator order awaiting POS session"),
                    note=_("Order %(order)s is accepted and awaiting a POS session. Open the mapped branch to complete POS accounting.",
                           order=order.external_order_id),
                )
                order.sudo()._with_delivery_internal_update().write({
                    "session_activity_id": activity.id,
                })

    def _finish_session_activity(self):
        for order in self:
            activity = order.session_activity_id.sudo().exists()
            if activity:
                activity.action_feedback(feedback=_("POS session wait ended."))
            order.sudo()._with_delivery_internal_update().write({
                "session_activity_id": False,
            })

    def _try_sync_terminal_pos_order(self):
        self.ensure_one()
        try:
            with self.env.cr.savepoint():
                self._sync_terminal_pos_order()
        except UserError as error:
            # Preserve authoritative delivery events while accounting is blocked.
            self._set_pos_processing_result("blocked", str(error))
        else:
            self._set_pos_processing_result("done")
        self._notify_pos_board("state_changed")

    def _check_external_pos_payments(self, order):
        if self.contract_version == 1 and self.payment_allocation_ids.filtered(
            lambda allocation: allocation.collected_by == "aggregator" and allocation.pos_payment_id
        ):
            raise UserError(_(
                "Review platform-collected payments before cancellation. "
                "A local POS refund does not return money through the platform.",
            ))
        payments = order.payment_ids
        external = payments.filtered(
            lambda payment: payment.transaction_id or payment.payment_ref_no
            or payment.payment_status or payment.payment_method_id.use_payment_terminal,
        )
        if "online_account_payment_id" in payments._fields:
            external |= payments.filtered("online_account_payment_id")
        if external:
            raise UserError(_(
                "Review captured or terminal payments before cancellation. "
                "A delivery cancellation cannot reverse an external payment.",
            ))

    def _sync_terminal_pos_order(self):
        self.ensure_one()
        order = self.pos_order_id
        if order:
            self.env.cr.execute("SELECT id FROM pos_order WHERE id = %s FOR UPDATE", [order.id])
            order.invalidate_recordset()
        if self.state == "delivered":
            order = order or self.create_pos_order()
            if order.state == "cancel":
                raise UserError(_("The delivered order is linked to a cancelled POS order."))
            if order.state == "draft":
                if order.session_id.state in ("closing_control", "closed"):
                    raise UserError(_("Review the closing or closed POS session before completion."))
                if self.currency_id.compare_amounts(order.amount_paid, order.amount_total):
                    raise UserError(_("The POS order is not fully paid. Record the collected balance before completion."))
                order._compute_prices()
                if self._compare_provider_financial_amounts(self.amount_total, order.amount_total):
                    raise UserError(_("The POS total changed. Review it before completing delivery."))
                if self._compare_provider_financial_amounts(self.vat_amount, order.amount_tax):
                    raise UserError(_("The POS tax changed. Review it before completing delivery."))
                order._process_saved_order(False)
        elif self.state in ("cancelled", "rejected") and order:
            if order.state == "draft":
                if order.payment_ids.account_move_id or order.account_move:
                    raise UserError(_("Review the accounting documents before cancellation."))
                self._check_external_pos_payments(order)
                # Do not call _clean_payment_lines: pos_online_payment overrides
                # it to remove only online payments, leaving clearing rows behind.
                order.payment_ids.unlink()
                order._compute_prices()
                order.action_pos_order_cancel()
            elif order.state in ("paid", "done", "invoiced"):
                self._refund_cancelled_pos_order()

    def _refund_cancelled_pos_order(self):
        self.ensure_one()
        original = self.pos_order_id
        if self.pos_refund_order_id:
            if self.pos_refund_order_id.state not in ("paid", "done", "invoiced"):
                raise UserError(_("Review the existing delivery refund before retrying."))
            return self.pos_refund_order_id
        self._check_external_pos_payments(original)
        if self.env["pos.order"].search_count([
            ("lines.refunded_orderline_id", "in", original.lines.ids),
            ("state", "=", "draft"),
        ], limit=1):
            raise UserError(_("Complete or review the existing draft refund first."))
        original.lines.invalidate_recordset(["refunded_qty"])
        if not original.lines.filtered(lambda line: line.refunded_qty < line.qty):
            return self.env["pos.order"]
        payments = original.payment_ids.filtered(lambda payment: payment.amount > 0)
        collected = sum(payments.mapped("amount"))
        if not payments or not collected:
            raise UserError(_("Review the original payments before creating a delivery refund."))
        session = original.config_id.current_session_id
        if not session or session.state in ("closing_control", "closed"):
            raise UserError(_("Open a POS session before creating a delivery refund."))
        refund = original._refund()
        if original.account_move:
            # A paid and invoiced order needs a credit note as well as the POS
            # refund. Otherwise cancellation leaves the original invoice open
            # in the accounting history with no reversing document.
            refund.write({"to_invoice": True})
        remaining = refund.amount_total
        for index, payment in enumerate(payments):
            if payment.payment_method_id not in refund.config_id.payment_method_ids:
                raise UserError(_("Enable the original payment method on the refund POS."))
            amount = (
                remaining if index == len(payments) - 1
                else refund.currency_id.round(refund.amount_total * payment.amount / collected)
            )
            remaining -= amount
            refund.add_payment({
                "pos_order_id": refund.id,
                "payment_method_id": payment.payment_method_id.id,
                "amount": amount,
                "payment_date": fields.Datetime.now(),
            })
        refund._process_saved_order(False)
        if original.account_move and (
            not refund.account_move or refund.account_move.state != "posted"
        ):
            raise UserError(_("The POS refund did not create a posted credit note."))
        self.sudo()._with_delivery_internal_update().write({"pos_refund_order_id": refund.id})
        return refund

    def action_retry_pos_processing(self):
        self.check_access("write")
        if not self.env.user.has_group("bs_pos_delivery_core.group_delivery_manager"):
            raise AccessError(_("Only a delivery manager can retry POS accounting."))
        if self.filtered(lambda order: order.company_id not in self.env.companies):
            raise AccessError(_("The delivery order belongs to an unavailable company."))
        # Same narrow elevation as manual delivery actions, after ACL/company checks.
        for order in self.sudo()._with_delivery_actor(self.env.user):
            self.env.cr.execute(
                "SELECT id FROM bs_delivery_order WHERE id = %s FOR UPDATE", [order.id],
            )
            order.invalidate_recordset()
            if order.state in ("delivered", "cancelled", "rejected"):
                order._try_sync_terminal_pos_order()
            elif order.state == "received" and order.pos_processing_state == "blocked":
                if order.connection_id.delivery_accept_action != "accept":
                    raise UserError(_("This order requires manual acceptance before POS processing."))
                order._apply_initial_acceptance_policy()
                if order.state != "accepted":
                    raise UserError(_("The order could not be accepted under the current delivery policy."))
            elif order.state in ("accepted", "preparing", "ready", "picked_up"):
                if order.pos_processing_state == "done":
                    continue
                if order.acceptance_deadline_at:
                    order._resume_awaiting_pos_session(retry_blocked=True)
                else:
                    pos_order = order.create_pos_order()
                    if pos_order.state == "draft":
                        order._finalize_accepted_pos_order()
                    elif (pos_order.state == "done" and pos_order.account_move
                          and pos_order.account_move.state == "posted"
                          and not order.currency_id.compare_amounts(
                              pos_order.amount_paid, pos_order.amount_total)
                          and not order._compare_provider_financial_amounts(
                              order.amount_total, pos_order.amount_total)
                          and not order._compare_provider_financial_amounts(
                              order.amount_total, pos_order.account_move.amount_total)
                          and order.currency_id.is_zero(pos_order.account_move.amount_residual)):
                        order._set_pos_processing_result("done")
                    else:
                        raise UserError(_("The existing POS order needs accounting review."))
                order._notify_pos_board("state_changed")
        return True

    def _pos_board_values(self):
        return {
            **super()._pos_board_values(),
            "pos_processing_state": self.pos_processing_state,
            "pos_processing_error": self.pos_processing_error,
            "sync_state": self.sync_state,
        }
