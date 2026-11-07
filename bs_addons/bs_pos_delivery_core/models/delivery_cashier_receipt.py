"""One cashier POS receipt print request per imported delivery order."""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


RECEIPT_MAX_AGE_MINUTES = 15
_logger = logging.getLogger(__name__)


class DeliveryCashierReceipt(models.Model):
    _inherit = "bs.delivery.order"

    cashier_receipt_state = fields.Selection(
        [
            ("not_requested", "Not Requested"),
            ("pending", "Pending"),
            ("attempting", "Printing"),
            ("sent", "POS Print Requested"),
            ("error", "Print Error"),
        ],
        default="not_requested", required=True, readonly=True, copy=False, index=True,
    )
    cashier_receipt_requested_at = fields.Datetime(readonly=True, copy=False)
    cashier_receipt_last_attempt_at = fields.Datetime(readonly=True, copy=False)
    cashier_receipt_sent_at = fields.Datetime(readonly=True, copy=False)
    cashier_receipt_attempts = fields.Integer(default=0, readonly=True, copy=False)
    cashier_receipt_error = fields.Text(readonly=True, copy=False)
    cashier_receipt_claim_key = fields.Char(readonly=True, copy=False)
    cashier_receipt_claim_until = fields.Datetime(readonly=True, copy=False)
    cashier_receipt_manual_retry = fields.Boolean(readonly=True, copy=False)
    cashier_receipt_can_retry = fields.Boolean(compute="_compute_cashier_receipt_can_retry")

    @api.depends(
        "pos_order_id.nb_print", "cashier_receipt_state", "cashier_receipt_attempts",
    )
    def _compute_cashier_receipt_can_retry(self):
        for order in self:
            order.cashier_receipt_can_retry = bool(
                order.pos_order_id
                and not order.pos_order_id.nb_print
                and order.cashier_receipt_state in ("error", "attempting")
            )

    def _request_cashier_receipt_autoprint(self):
        """Queue only new, completed imports on POS branches with auto receipts."""
        for order in self:
            pos_order = order.pos_order_id
            config = order.pos_config_id
            if (
                order.cashier_receipt_state != "not_requested"
                or not pos_order
                or pos_order.nb_print
                or not config.iface_print_auto
                or order.pos_processing_state != "done"
                or pos_order.state != "done"
                or pos_order.account_move.state != "posted"
            ):
                continue
            order.sudo()._with_delivery_internal_update().write({
                "cashier_receipt_state": "pending",
                "cashier_receipt_requested_at": fields.Datetime.now(),
            })
            try:
                with self.env.cr.savepoint():
                    order._dispatch_cashier_receipt_job()
            except Exception:
                # Polling can still deliver the saved job if a bus wakeup fails.
                _logger.exception("Could not notify POS of cashier receipt %s", order.id)
        return True

    def _dispatch_cashier_receipt_job(self):
        self.ensure_one()
        pos_order = self.pos_order_id
        session = pos_order.session_id
        config = session.config_id
        if not session or not config:
            return False
        products = pos_order.lines.product_id
        records = {
            "product.template": products.product_tmpl_id.ids,
            "product.product": products.ids,
            "pos.order": pos_order.ids,
            "pos.order.line": pos_order.lines.ids,
        }
        if pos_order.payment_ids:
            records["pos.payment"] = pos_order.payment_ids.ids
        config.sudo().notify_synchronisation(session.id, 0, records)
        config.sudo()._notify("BS_DELIVERY_POS_BOARD", {
            "event": "cashier_receipt_print",
            "cashier_receipt_print": True,
            "order_id": self.id,
            "pos_order_id": pos_order.id,
        })
        return True

    def _cashier_receipt_is_stale(self, now=None):
        self.ensure_one()
        return bool(
            self.cashier_receipt_requested_at
            and self.cashier_receipt_requested_at < fields.Datetime.subtract(
                now or fields.Datetime.now(), minutes=RECEIPT_MAX_AGE_MINUTES,
            )
        )

    def _expire_cashier_receipt(self):
        self.ensure_one()
        self.sudo()._with_delivery_internal_update().write({
            "cashier_receipt_state": "error",
            "cashier_receipt_error": _(
                "The cashier receipt request is over 15 minutes old. "
                "Check the printer and retry manually."
            ),
            "cashier_receipt_manual_retry": False,
            "cashier_receipt_claim_key": False,
            "cashier_receipt_claim_until": False,
        })

    def _cashier_receipt_is_eligible(self, config):
        self.ensure_one()
        pos_order = self.pos_order_id
        session = config.current_session_id
        return bool(
            self.company_id == config.company_id
            and self.pos_config_id == config
            and self.state in ("accepted", "preparing", "ready", "picked_up", "delivered")
            and self.pos_processing_state == "done"
            and self.cashier_receipt_state == "pending"
            and (not self.cashier_receipt_attempts or self.cashier_receipt_manual_retry)
            and config.iface_print_auto
            and session and session.state in ("opening_control", "opened")
            and pos_order and pos_order.session_id == session
            and pos_order.state == "done"
            and pos_order.account_move.state == "posted"
        )

    @api.model
    def get_pending_cashier_receipts(self, pos_config_id, limit=20):
        config = self._check_pos_board_access(pos_config_id)
        if not config.iface_print_auto or not config.current_session_id:
            return []
        try:
            row_limit = min(max(int(limit or 20), 1), 50)
        except (TypeError, ValueError):
            row_limit = 20
        candidates = self.sudo().search([
            ("company_id", "=", config.company_id.id),
            ("pos_config_id", "=", config.id),
            ("pos_order_id.session_id", "=", config.current_session_id.id),
            ("cashier_receipt_state", "=", "pending"),
            "|", ("cashier_receipt_attempts", "=", 0),
            ("cashier_receipt_manual_retry", "=", True),
        ], order="cashier_receipt_requested_at, id", limit=row_limit)
        jobs = []
        for order in candidates:
            if not order._cashier_receipt_is_eligible(config):
                continue
            if order.pos_order_id.nb_print:
                order._mark_cashier_receipt_sent()
                continue
            if order._cashier_receipt_is_stale():
                order._expire_cashier_receipt()
                continue
            jobs.append({"order_id": order.id, "pos_order_id": order.pos_order_id.id})
        return jobs

    def _mark_cashier_receipt_sent(self):
        self.ensure_one()
        self.sudo()._with_delivery_internal_update().write({
            "cashier_receipt_state": "sent",
            "cashier_receipt_sent_at": fields.Datetime.now(),
            "cashier_receipt_error": False,
            "cashier_receipt_claim_key": False,
            "cashier_receipt_claim_until": False,
            "cashier_receipt_manual_retry": False,
        })

    @api.model
    def claim_cashier_receipt(self, order_id, pos_config_id, claim_key):
        config = self._check_pos_board_access(pos_config_id)
        normalized_claim = str(claim_key or "").strip()[:128]
        if len(normalized_claim) < 16:
            raise ValidationError(_("A valid cashier receipt claim key is required."))
        try:
            order_key = int(order_id or 0)
        except (TypeError, ValueError):
            order_key = 0
        order = self.sudo().search([
            ("id", "=", order_key),
            ("company_id", "=", config.company_id.id),
            ("pos_config_id", "=", config.id),
        ], limit=1)
        if not order:
            raise AccessError(_("The cashier receipt job is not available."))
        self.env.cr.execute(
            "SELECT id FROM bs_delivery_order WHERE id = %s FOR UPDATE", [order.id],
        )
        order.invalidate_recordset()
        if not order._cashier_receipt_is_eligible(config):
            return False
        now = fields.Datetime.now()
        if order.pos_order_id.nb_print:
            order._mark_cashier_receipt_sent()
            return False
        if order._cashier_receipt_is_stale(now):
            order._expire_cashier_receipt()
            return False
        if (
            order.cashier_receipt_claim_until
            and order.cashier_receipt_claim_until > now
            and order.cashier_receipt_claim_key != normalized_claim
        ):
            return False
        order._with_delivery_internal_update().write({
            "cashier_receipt_state": "attempting",
            "cashier_receipt_manual_retry": False,
            "cashier_receipt_claim_key": normalized_claim,
            "cashier_receipt_claim_until": fields.Datetime.add(now, seconds=120),
            "cashier_receipt_last_attempt_at": now,
            "cashier_receipt_attempts": order.cashier_receipt_attempts + 1,
        })
        return True

    @api.model
    def acknowledge_cashier_receipt(
        self, order_id, pos_config_id, claim_key, success, error=None,
    ):
        config = self._check_pos_board_access(pos_config_id)
        if not isinstance(success, bool):
            raise ValidationError(_("A boolean cashier receipt result is required."))
        normalized_claim = str(claim_key or "").strip()[:128]
        if len(normalized_claim) < 16:
            raise ValidationError(_("A valid cashier receipt claim key is required."))
        try:
            order_key = int(order_id or 0)
        except (TypeError, ValueError):
            order_key = 0
        order = self.sudo().search([
            ("id", "=", order_key),
            ("company_id", "=", config.company_id.id),
            ("pos_config_id", "=", config.id),
        ], limit=1)
        if not order:
            raise AccessError(_("The cashier receipt job is not available."))
        self.env.cr.execute(
            "SELECT id FROM bs_delivery_order WHERE id = %s FOR UPDATE", [order.id],
        )
        order.invalidate_recordset()
        if order.cashier_receipt_state == "sent":
            return True
        if (
            order.cashier_receipt_state != "attempting"
            or order.cashier_receipt_claim_key != normalized_claim
        ):
            raise AccessError(_("This cashier receipt is leased by another POS device."))
        if success:
            order._mark_cashier_receipt_sent()
        else:
            order._with_delivery_internal_update().write({
                "cashier_receipt_state": "error",
                "cashier_receipt_error": str(error or _("Unknown print error"))[:2000],
                "cashier_receipt_claim_key": False,
                "cashier_receipt_claim_until": False,
            })
        return True

    def action_retry_cashier_receipt(self):
        self.check_access("write")
        if not self.env.user.has_group("bs_pos_delivery_core.group_delivery_manager"):
            raise AccessError(_("Only a delivery manager can retry cashier receipt printing."))
        if self.filtered(lambda order: order.company_id not in self.env.companies):
            raise AccessError(_("The delivery order belongs to an unavailable company."))
        for order in self.sudo():
            self.env.cr.execute(
                "SELECT id FROM bs_delivery_order WHERE id = %s FOR UPDATE", [order.id],
            )
            order.invalidate_recordset()
            if not order.cashier_receipt_can_retry:
                raise UserError(_("This cashier receipt is not eligible for a retry."))
            if (
                not order.pos_config_id.iface_print_auto
                or order.pos_order_id.session_id != order.pos_config_id.current_session_id
            ):
                raise UserError(_(
                    "Open the original POS session and enable automatic receipt printing "
                    "before retrying this cashier receipt."
                ))
            if (
                order.cashier_receipt_claim_until
                and order.cashier_receipt_claim_until > fields.Datetime.now()
            ):
                raise UserError(_("A POS device is still handling this cashier receipt."))
            order._with_delivery_internal_update().write({
                "cashier_receipt_state": "pending",
                "cashier_receipt_manual_retry": True,
                "cashier_receipt_requested_at": fields.Datetime.now(),
                "cashier_receipt_error": False,
                "cashier_receipt_claim_key": False,
                "cashier_receipt_claim_until": False,
            })
            try:
                with self.env.cr.savepoint():
                    order._dispatch_cashier_receipt_job()
            except Exception:
                _logger.exception("Could not notify POS of cashier receipt retry %s", order.id)
        return True
