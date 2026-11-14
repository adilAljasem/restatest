import copy
import logging
import time

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.service.model import PG_CONCURRENCY_EXCEPTIONS_TO_RETRY

from ..services.exceptions import BlendAPIError
from ..services.pos_api import BlendPOSAPIService

_logger = logging.getLogger(__name__)

REPROCESS_MODES = [
    ("new", "Not yet fulfilled — use normal branch settings"),
    ("fulfilled", "Already fulfilled — recover a draft for review"),
]


class BlendIntegrationLog(models.Model):
    _inherit = "blend.integration.log"

    reprocess_mode = fields.Selection(REPROCESS_MODES, readonly=True)
    reprocessed_by_id = fields.Many2one("res.users", string="Reprocessed By", readonly=True)
    reprocessed_at = fields.Datetime(string="Reprocessed At", readonly=True)

    def _check_inbound_reprocess(self):
        self.ensure_one()
        self.check_access("write")
        if self.company_id not in self.env.companies:
            raise AccessError(_("Select the log's company before reprocessing this order."))
        self.config_id.check_access("read")
        if self.direction != "inbound" or self.event != "pos.order.create":
            raise UserError(_("Only inbound POS order creation requests can be reprocessed here."))
        if self.config_id.company_id != self.company_id:
            raise UserError(_("The log and POS branch must belong to the same company."))
        payload = self.request_payload
        if not isinstance(payload, dict):
            raise UserError(_("The log does not contain a valid original request payload."))
        reference = payload.get("partner_reference")
        if (not isinstance(reference, str) or not reference.strip()
                or reference.strip() != self.external_reference
                or self.idempotency_key != "pos_order_create:%s:%s" % (self.config_id.id, reference.strip())
                or str(payload.get("branch_id", "")).strip() != self.config_id.blend_branch_id
                or str(payload.get("brand_id", "")).strip() != self.config_id.blend_brand_id):
            raise UserError(_("The original payload's branch, brand or order reference does not match this log."))
        if self.order_id and (self.order_id.config_id != self.config_id
                              or self.order_id.company_id != self.company_id
                              or self.order_id.blend_partner_reference != reference.strip()):
            raise UserError(_("The order linked to this log does not match the original request."))

    def action_reprocess_inbound(self):
        self._check_inbound_reprocess()
        if self.state != "failed":
            raise UserError(_("Only failed inbound order requests can be reprocessed. Refresh this log."))
        return {
            "type": "ir.actions.act_window", "name": _("Reprocess Incoming Order"),
            "res_model": "blend.inbound.reprocess.wizard", "view_mode": "form", "target": "new",
            "context": {"default_log_id": self.id},
        }

    def _reprocessed_order_action(self):
        self.order_id.check_access("read")
        return {
            "type": "ir.actions.act_window", "name": _("Recovered Blend Order"),
            "res_model": "pos.order", "view_mode": "form", "res_id": self.order_id.id,
            "target": "current",
        }

    def _reprocess_inbound(self, mode):
        self._check_inbound_reprocess()
        if mode not in dict(REPROCESS_MODES):
            raise UserError(_("Choose how to reprocess this order."))
        # The same lock/order is used by incoming HTTP requests. Unique log and
        # order keys also protect concurrent requests under snapshot isolation.
        self.lock_idempotency_key(self.idempotency_key)
        self.lock_for_update()
        self.invalidate_recordset()
        self._check_inbound_reprocess()
        if self.state == "success" and self.order_id:
            return self._reprocessed_order_action()  # second click on an old wizard
        if self.state != "failed":
            raise UserError(_("This request is no longer failed. Refresh the integration log."))
        if not self.config_id.active or not self.config_id.blend_enabled:
            raise UserError(_("Enable the original Blend POS branch before reprocessing its orders."))

        started = time.perf_counter()
        now = fields.Datetime.now()
        self.write({"reprocess_mode": mode, "reprocessed_by_id": self.env.uid, "reprocessed_at": now})
        service = BlendPOSAPIService(self.with_company(self.company_id).env, self.config_id)
        try:
            # Roll back all partial imports on failure, then preserve the failed
            # attempt outside this savepoint instead of raising an RPC rollback.
            with self.env.cr.savepoint():
                service.create_order(copy.deepcopy(self.request_payload), recover_fulfilled=mode == "fulfilled")
        except (AccessError, *PG_CONCURRENCY_EXCEPTIONS_TO_RETRY):
            raise
        except Exception as error:
            if not isinstance(error, (BlendAPIError, UserError)):
                _logger.exception("Unexpected inbound reprocessing failure for Blend log %s", self.id)
                error = UserError(_("Order reprocessing failed unexpectedly. Review the server log before retrying."))
            self.write({"attempt_count": self.attempt_count + 1, "started_at": now, "last_attempt_at": now})
            service._fail_log(self, error, started)
            return {
                "type": "ir.actions.client", "tag": "display_notification",
                "params": {"title": _("Order reprocessing failed"), "message": self.error_message,
                           "type": "danger", "sticky": True,
                           "next": {"type": "ir.actions.act_window_close"}},
            }
        self.invalidate_recordset()
        return self._reprocessed_order_action()


class BlendInboundReprocessWizard(models.TransientModel):
    _name = "blend.inbound.reprocess.wizard"
    _description = "Reprocess a Failed Incoming Blend Order"

    log_id = fields.Many2one("blend.integration.log", required=True, readonly=True, ondelete="cascade")
    config_id = fields.Many2one(related="log_id.config_id")
    external_reference = fields.Char(related="log_id.external_reference")
    mode = fields.Selection(REPROCESS_MODES, required=True, default="fulfilled", string="Order Situation")

    def action_reprocess(self):
        self.ensure_one()
        self.check_access("write")
        return self.log_id._reprocess_inbound(self.mode)


class PosOrder(models.Model):
    _inherit = "pos.order"

    blend_recovered_fulfilled = fields.Boolean(
        string="Recovered Already Fulfilled Order", readonly=True, copy=False,
        help="Recovered for local review. Automatic acceptance, Blend status notifications and Blend kitchen dispatch are disabled.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and (any("blend_recovered_fulfilled" in vals for vals in vals_list)
                                or "default_blend_recovered_fulfilled" in self.env.context):
            raise AccessError(_("The Blend recovery marker is managed by the integration."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and "blend_recovered_fulfilled" in vals:
            raise AccessError(_("The Blend recovery marker is managed by the integration."))
        return super().write(vals)

    @api.model
    def _load_pos_data_fields(self, config):
        # The recovery marker is server-owned; ordinary cashier orders must not
        # round-trip it as a writable field through sync_from_ui.
        names = super()._load_pos_data_fields(config) or self.fields_get(attributes=["type"])
        return [name for name in names if name != "blend_recovered_fulfilled"]

    def _blend_auto_accept(self):
        return super(PosOrder, self.filtered(lambda order: not order.blend_recovered_fulfilled))._blend_auto_accept()

    def _blend_enqueue_status(self, status):
        return super(PosOrder, self.filtered(lambda order: not order.blend_recovered_fulfilled))._blend_enqueue_status(status)

    def _blend_enqueue_order_created(self):
        return super(PosOrder, self.filtered(lambda order: not order.blend_recovered_fulfilled))._blend_enqueue_order_created()

    def _blend_send_to_preparation(self):
        self.ensure_one()
        if not self.blend_recovered_fulfilled:
            return super()._blend_send_to_preparation()

    def _resta_kds_targets(self, paid_now=False):
        self.ensure_one()
        if (self.blend_recovered_fulfilled
                or (self.env.context.get("blend_financial_processing")
                    and self.blend_partner_reference
                    and self.blend_status in ("ready_to_pick_up", "at_pick_up", "on_the_way", "completed"))):
            # A remotely progressed or recovered order must not create a new kitchen ticket.
            return None
        return super()._resta_kds_targets(paid_now)

    def _blend_queue_kitchen_print(self):
        self.ensure_one()
        if not self.blend_recovered_fulfilled:
            return super()._blend_queue_kitchen_print()
