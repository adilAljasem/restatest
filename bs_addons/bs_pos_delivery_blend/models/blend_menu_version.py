"""Blend product notifications on the existing menu synchronization queue."""

import logging
from datetime import timedelta

from odoo import _, api, fields, models

from ..services.menu_notifications import BlendMenuNotificationService
from odoo.addons.bs_pos_delivery_core.exceptions import DeliveryBusinessError

_logger = logging.getLogger(__name__)


class BlendMenuVersion(models.Model):
    _inherit = "bs.delivery.menu.version"

    blend_change_id = fields.Char(index=True, copy=False)
    blend_entity_type = fields.Char(copy=False)
    blend_entity_id = fields.Char(index=True, copy=False)
    blend_destination_key = fields.Char(copy=False)
    blend_attempt_count = fields.Integer(default=0, copy=False)
    blend_next_retry_at = fields.Datetime(index=True, copy=False)

    _blend_notification_unique = models.Constraint(
        "UNIQUE(connection_id, blend_change_id, blend_entity_type, blend_entity_id)",
        "A Blend menu change can only queue one notification per connection and entity.",
    )

    def _dispatch_menu_sync(self):
        self.ensure_one()
        if not self.blend_change_id:
            return super()._dispatch_menu_sync()
        connection = self.connection_id
        if (
            connection.provider_id.code != "blend"
            or self.direction != "push"
            or not connection.active
            or connection.state != "connected"
            or not connection.blend_outbound_webhook_url
            or not connection.pos_config_ids.filtered(lambda branch: branch.active and branch.blend_enabled)
        ):
            raise DeliveryBusinessError(_("Blend menu notification connection is no longer available."))
        if self.blend_destination_key != BlendMenuNotificationService.destination_key(connection.sudo()):
            raise DeliveryBusinessError(_("Blend menu notification destination changed. Save the product again to create a new notification."))
        self._resolve_operation()
        return connection._dispatch(
            "push_menu",
            entity_type=self.blend_entity_type,
            entity_id=self.blend_entity_id,
            menu_version=self,
            idempotency_key="blend-menu-version:%s" % self.id,
        )

    @api.model
    def _cron_process_blend_menu_notifications(self, limit=50):
        """Send committed Blend jobs only; leave other providers' queues alone."""
        now = fields.Datetime.now()
        stale = self.sudo().search([
            ("provider_id.code", "=", "blend"),
            ("blend_change_id", "!=", False),
            ("state", "=", "processing"),
            ("started_at", "<=", now - timedelta(minutes=10)),
        ], limit=limit).try_lock_for_update()
        for job in stale:
            attempts = job.blend_attempt_count + 1
            job.write({
                "state": "failed",
                "blend_attempt_count": attempts,
                "blend_next_retry_at": now if attempts < 3 else False,
                "last_error": "Recovered after an interrupted Blend menu notification.",
            })
        jobs = self.sudo().search([
            ("provider_id.code", "=", "blend"),
            ("blend_change_id", "!=", False),
            ("blend_attempt_count", "<", 3),
            "|",
            ("state", "=", "queued"),
            "&",
            ("state", "=", "failed"),
            ("blend_next_retry_at", "<=", now),
        ], order="id", limit=limit).try_lock_for_update()
        processed = 0
        for job in jobs:
            with self.env.cr.savepoint():
                if job.state == "failed":
                    job.write({"state": "queued"})
                if not job._claim_for_processing():
                    continue
                try:
                    with self.env.cr.savepoint():
                        result = job._dispatch_menu_sync()
                        job._mark_sync_done(result)
                except Exception as error:
                    _logger.exception("Blend menu notification %s failed", job.id)
                    attempts = job.blend_attempt_count + 1
                    job.write({
                        "state": "failed",
                        "blend_attempt_count": attempts,
                        "blend_next_retry_at": now + timedelta(minutes=2 ** attempts) if attempts < 3 else False,
                        "completed_at": fields.Datetime.now(),
                        "last_error": str(error)[:4000],
                    })
            processed += 1
            if self.env.context.get("cron_id") and not self.env["ir.cron"]._commit_progress(1):
                break
        return processed
