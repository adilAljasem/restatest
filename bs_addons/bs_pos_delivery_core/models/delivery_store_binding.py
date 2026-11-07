import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class DeliveryStoreBinding(models.Model):
    """Map one provider-side store to the POS branch that owns its orders."""

    _name = "bs.delivery.store.binding"
    _description = "POS Delivery Store Binding"
    _order = "company_id, connection_id, sequence, name, id"
    _check_company_auto = True

    name = fields.Char(compute="_compute_name", store=True)
    sequence = fields.Integer(default=10)
    connection_id = fields.Many2one(
        "bs.delivery.connection",
        required=True,
        index=True,
        ondelete="cascade",
        check_company=True,
    )
    provider_id = fields.Many2one(
        related="connection_id.provider_id",
        store=True,
        index=True,
        readonly=True,
    )
    company_id = fields.Many2one(
        related="connection_id.company_id",
        store=True,
        index=True,
        readonly=True,
    )
    pos_config_id = fields.Many2one(
        "pos.config",
        string="Point of Sale Branch",
        required=True,
        index=True,
        ondelete="restrict",
        check_company=True,
    )
    remote_store_id = fields.Char(required=True, index=True)
    remote_vendor_id = fields.Char(index=True)
    remote_integration_id = fields.Char(index=True)
    active = fields.Boolean(default=True)
    is_sandbox = fields.Boolean(compute="_compute_is_sandbox", store=True)

    paused = fields.Boolean(default=False, index=True)
    paused_until = fields.Datetime(copy=False)
    pause_reason = fields.Text(copy=False)
    pause_reject_reason_id = fields.Many2one(
        "bs.delivery.reject.reason",
        string="Pause Rejection Reason",
        ondelete="restrict",
        check_company=True,
    )
    is_paused = fields.Boolean(compute="_compute_is_paused")

    work_time_enabled = fields.Boolean(
        related="pos_config_id.delivery_work_time_enabled",
        readonly=False,
        string="Work Time",
    )
    work_start_time = fields.Float(
        related="pos_config_id.delivery_work_start_time",
        readonly=False,
        string="Start 1",
    )
    work_end_time = fields.Float(
        related="pos_config_id.delivery_work_end_time",
        readonly=False,
        string="End 1",
    )
    second_shift_enabled = fields.Boolean(
        related="pos_config_id.delivery_second_shift_enabled",
        readonly=False,
        string="2nd Shift",
    )
    work_start_time_2 = fields.Float(
        related="pos_config_id.delivery_work_start_time_2",
        readonly=False,
        string="Start 2",
    )
    work_end_time_2 = fields.Float(
        related="pos_config_id.delivery_work_end_time_2",
        readonly=False,
        string="End 2",
    )
    is_open_now = fields.Boolean(
        compute="_compute_is_open_now",
        string="Open Now",
        help="Whether this branch currently accepts delivery orders "
        "(active, not paused, and inside its work shifts or with an open session).",
    )
    last_availability_synced = fields.Boolean(
        string="Last Synced Availability",
        readonly=True,
        copy=False,
    )
    last_availability_sync_at = fields.Datetime(
        string="Last Synced to Aggregator",
        readonly=True,
        copy=False,
    )

    _remote_store_unique = models.Constraint(
        "UNIQUE(connection_id, remote_store_id) DEFERRABLE INITIALLY DEFERRED",
        "A remote store can only be mapped once per delivery connection.",
    )

    def init(self):
        """Reconcile legacy POS links for connections with store mappings."""
        self.env.cr.execute(
            """
            DELETE FROM bs_delivery_connection_pos_config_rel AS relation
             WHERE EXISTS (
                SELECT 1 FROM bs_delivery_store_binding AS store
                 WHERE store.connection_id = relation.connection_id
             )
               AND NOT EXISTS (
                SELECT 1 FROM bs_delivery_store_binding AS store
                 WHERE store.connection_id = relation.connection_id
                   AND store.pos_config_id = relation.pos_config_id
                   AND store.active
             )
            """
        )
        self.env.cr.execute(
            """
            INSERT INTO bs_delivery_connection_pos_config_rel
                        (connection_id, pos_config_id)
                 SELECT DISTINCT store.connection_id, store.pos_config_id
                   FROM bs_delivery_store_binding AS store
                  WHERE store.active
                    AND NOT EXISTS (
                        SELECT 1
                          FROM bs_delivery_connection_pos_config_rel AS relation
                         WHERE relation.connection_id = store.connection_id
                           AND relation.pos_config_id = store.pos_config_id
                    )
            """
        )

    @api.depends(
        "connection_id.name",
        "pos_config_id.name",
        "remote_store_id",
    )
    def _compute_name(self):
        for binding in self:
            parts = [
                binding.connection_id.display_name,
                binding.remote_store_id,
                binding.pos_config_id.display_name,
            ]
            binding.name = " / ".join(part for part in parts if part)

    @api.depends("connection_id.environment")
    def _compute_is_sandbox(self):
        for binding in self:
            binding.is_sandbox = binding.connection_id.environment == "test"

    @api.depends("paused", "paused_until")
    def _compute_is_paused(self):
        now = fields.Datetime.now()
        for binding in self:
            binding.is_paused = bool(
                binding.paused
                and (not binding.paused_until or binding.paused_until > now)
            )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(
            [self._normalize_store_values(values) for values in vals_list]
        )
        if not self.env.context.get("bs_delivery_defer_store_sync"):
            records.mapped("connection_id")._synchronize_pos_scope_from_store_bindings(
                authoritative=True
            )
        return records

    def write(self, vals):
        previous_connections = self.mapped("connection_id")
        result = super().write(self._normalize_store_values(vals))
        if not self.env.context.get("bs_delivery_defer_store_sync"):
            (
                previous_connections | self.mapped("connection_id")
            )._synchronize_pos_scope_from_store_bindings(authoritative=True)
        return result

    def unlink(self):
        connections = self.mapped("connection_id")
        result = super().unlink()
        if not self.env.context.get("bs_delivery_defer_store_sync"):
            connections._synchronize_pos_scope_from_store_bindings(authoritative=True)
        return result

    @api.model
    def _normalize_store_values(self, values):
        values = dict(values)
        for field_name in (
            "remote_store_id",
            "remote_vendor_id",
            "remote_integration_id",
        ):
            if field_name in values:
                values[field_name] = str(values[field_name] or "").strip() or False
        if values.get("paused") is False:
            values.setdefault("paused_until", False)
            values.setdefault("pause_reason", False)
        return values

    @api.constrains("remote_store_id")
    def _check_remote_store_id(self):
        for binding in self:
            if not (binding.remote_store_id or "").strip():
                raise ValidationError(_("Remote Store ID cannot be empty."))

    @api.constrains(
        "connection_id",
        "pos_config_id",
        "pause_reject_reason_id",
    )
    def _check_store_company_scope(self):
        for binding in self:
            if binding.pos_config_id.company_id != binding.company_id:
                raise ValidationError(
                    _("The POS branch must belong to the delivery connection company.")
                )
            reason = binding.pause_reject_reason_id
            if reason and reason.company_id and reason.company_id != binding.company_id:
                raise ValidationError(
                    _("The pause rejection reason must be shared or belong to the same company.")
                )
            if reason and reason.provider_id and reason.provider_id != binding.provider_id:
                raise ValidationError(
                    _("The pause rejection reason belongs to another delivery provider.")
                )

    def pause_receiving(self, *, reason=None, until=None):
        values = {
            "paused": True,
            "paused_until": until or False,
            "pause_reason": str(reason or "").strip() or False,
        }
        return self.write(values)

    def resume_receiving(self):
        return self.write(
            {
                "paused": False,
                "paused_until": False,
                "pause_reason": False,
            }
        )

    @api.depends(
        "active",
        "paused",
        "paused_until",
        "pos_config_id.active",
        "pos_config_id.delivery_work_time_enabled",
        "pos_config_id.delivery_work_start_time",
        "pos_config_id.delivery_work_end_time",
        "pos_config_id.delivery_second_shift_enabled",
        "pos_config_id.delivery_work_start_time_2",
        "pos_config_id.delivery_work_end_time_2",
    )
    def _compute_is_open_now(self):
        for store in self:
            store.is_open_now = bool(store.pos_config_id) and store.is_open_for_delivery()

    def is_in_delivery_work_time(self, at=None):
        self.ensure_one()
        if not self.pos_config_id:
            return False
        return self.pos_config_id.is_in_delivery_work_time(at=at)

    def is_open_for_delivery(self, at=None):
        """Provider-neutral store-open hook.

        When work time is enabled, the store is open inside one of its work
        shifts (even if the POS session is currently closed). Otherwise it
        falls back to checking for an opened POS session.
        """
        self.ensure_one()
        if not self.active or self.is_paused or not self.pos_config_id.active:
            return False
        if self.work_time_enabled:
            return self.is_in_delivery_work_time(at=at)
        session = self.pos_config_id.current_session_id
        return bool(session and session.state in ("opening_control", "opened"))

    def _push_availability(self, adapter, is_open):
        """Send the open/closed status to the provider and remember it."""
        self.ensure_one()
        adapter.update_store_availability(self.connection_id, self, is_open)
        self.sudo().with_context(bs_delivery_defer_store_sync=True).write({
            "last_availability_synced": is_open,
            "last_availability_sync_at": fields.Datetime.now(),
        })

    def action_sync_availability(self):
        """Explicitly notify the delivery provider about this branch's availability."""
        self.ensure_one()
        adapter = self.connection_id._get_adapter()
        if not adapter.supports("update_store_availability"):
            return self.connection_id._notification(
                _("%s does not support store availability updates.")
                % self.provider_id.display_name,
                notification_type="warning",
            )
        is_open = self.is_open_for_delivery()
        self._push_availability(adapter, is_open)
        return self.connection_id._notification(
            _("%(store)s sent as %(status)s.",
              store=self.pos_config_id.display_name or self.remote_store_id,
              status=_("Open") if is_open else _("Closed")),
            notification_type="success",
        )

    @api.model
    def _cron_sync_store_availability(self):
        """Push store open/closed changes (work shifts, pauses, sessions) to providers."""
        stores = self.sudo().search([
            ("active", "=", True),
            ("connection_id.state", "=", "connected"),
        ])
        for store in stores:
            try:
                adapter = store.connection_id._get_adapter()
                if not adapter.supports("update_store_availability"):
                    continue
                is_open = store.is_open_for_delivery()
                if not store.last_availability_sync_at or store.last_availability_synced != is_open:
                    with self.env.cr.savepoint():
                        store._push_availability(adapter, is_open)
            except Exception as error:
                _logger.warning("Failed to sync availability for store %s: %s", store.display_name, error)
        return True
