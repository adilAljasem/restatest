import psycopg2

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class DeliveryOrderBinding(models.Model):
    """Idempotent link between one external order and an Odoo POS order."""

    _name = "bs.delivery.order.binding"
    _description = "POS Delivery Order Binding"
    _rec_name = "external_order_id"
    _order = "create_date desc, id desc"
    _check_company_auto = True

    connection_id = fields.Many2one(
        "bs.delivery.connection",
        required=True,
        index=True,
        ondelete="restrict",
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
    pos_order_id = fields.Many2one(
        "pos.order",
        string="POS Order",
        index=True,
        ondelete="set null",
        check_company=True,
        copy=False,
    )
    external_order_id = fields.Char(required=True, index=True, copy=False)
    external_reference = fields.Char(index=True, copy=False)
    external_source = fields.Char(
        string="External Source",
        index=True,
        copy=False,
        help="Optional marketplace or aggregator name carried by the provider.",
    )
    external_status = fields.Char(index=True, copy=False)
    odoo_status = fields.Selection(
        [
            ("pending", "Pending"),
            ("accepted", "Accepted"),
            ("preparing", "Preparing"),
            ("ready", "Ready"),
            ("dispatched", "Dispatched"),
            ("completed", "Completed"),
            ("cancelled", "Cancelled"),
            ("rejected", "Rejected"),
        ],
        default="pending",
        required=True,
        index=True,
        copy=False,
    )
    sync_state = fields.Selection(
        [
            ("pending", "Pending"),
            ("synced", "Synced"),
            ("error", "Error"),
        ],
        default="pending",
        required=True,
        index=True,
        copy=False,
    )
    last_sync_date = fields.Datetime(readonly=True, copy=False)
    last_error = fields.Text(readonly=True, copy=False)
    log_ids = fields.One2many("bs.delivery.sync.log", "binding_id", string="Logs")
    log_count = fields.Integer(compute="_compute_log_count")

    _external_order_unique = models.Constraint(
        "UNIQUE(connection_id, external_order_id)",
        "An external order can only be bound once per delivery connection.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        normalized_vals_list = []
        for vals in vals_list:
            vals = dict(vals)
            if "external_order_id" in vals:
                vals["external_order_id"] = str(vals["external_order_id"] or "").strip()
            normalized_vals_list.append(vals)
        return super().create(normalized_vals_list)

    def write(self, vals):
        vals = dict(vals)
        if "external_order_id" in vals:
            vals["external_order_id"] = str(vals["external_order_id"] or "").strip()
        return super().write(vals)

    @api.constrains("external_order_id")
    def _check_external_order_id(self):
        for binding in self:
            if not binding.external_order_id:
                raise ValidationError(_("External Order ID cannot be empty."))

    @api.constrains("sync_state", "pos_order_id")
    def _check_synced_binding_has_order(self):
        for binding in self:
            if binding.sync_state == "synced" and not binding.pos_order_id:
                raise ValidationError(
                    _("A synced delivery binding must be linked to a POS order.")
                )

    def _compute_log_count(self):
        counts = {}
        if self.ids:
            counts = {
                binding.id: count
                for binding, count in self.env["bs.delivery.sync.log"]._read_group(
                    [("binding_id", "in", self.ids)],
                    ["binding_id"],
                    ["__count"],
                )
            }
        for binding in self:
            binding.log_count = counts.get(binding.id, 0)

    @api.model
    def get_or_create(self, connection, external_order_id, values=None):
        """Reserve an external order ID atomically.

        Returns ``(binding, created)``. The SQL constraint remains the source
        of truth if two workers race after the initial lookup.
        """
        connection.ensure_one()
        external_order_id = str(external_order_id or "").strip()
        if not external_order_id:
            raise ValidationError(_("External Order ID is required."))

        domain = [
            ("connection_id", "=", connection.id),
            ("external_order_id", "=", external_order_id),
        ]
        binding = self.search(domain, limit=1)
        if binding:
            return binding, False

        create_values = dict(values or {})
        create_values.update(
            {
                "connection_id": connection.id,
                "external_order_id": external_order_id,
            }
        )
        try:
            with self.env.cr.savepoint():
                binding = self.create(create_values)
            return binding, True
        except psycopg2.errors.UniqueViolation:
            binding = self.search(domain, limit=1)
            if not binding:
                raise
            return binding, False

    def mark_sync_pending(self):
        return self.write({"sync_state": "pending", "last_error": False})

    def mark_sync_success(self, *, external_status=None, odoo_status=None):
        values = {
            "sync_state": "synced",
            "last_sync_date": fields.Datetime.now(),
            "last_error": False,
        }
        if external_status is not None:
            values["external_status"] = external_status
        if odoo_status is not None:
            values["odoo_status"] = odoo_status
        return self.write(values)

    def mark_sync_error(self, error_message):
        return self.write(
            {
                "sync_state": "error",
                "last_sync_date": fields.Datetime.now(),
                "last_error": error_message,
            }
        )

    def action_view_logs(self):
        self.ensure_one()
        action = self.env.ref("bs_pos_delivery_core.action_bs_delivery_sync_log").read()[0]
        action["domain"] = [("binding_id", "=", self.id)]
        action["context"] = {"default_binding_id": self.id}
        return action
