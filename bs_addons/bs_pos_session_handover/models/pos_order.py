# -*- coding: utf-8 -*-

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.fields import Command, Domain

PINO_FINAL_STATES = ("paid", "done", "invoiced")


class PosOrder(models.Model):
    _inherit = "pos.order"

    pino_is_session_transferred = fields.Boolean(
        string="Transferred Between Sessions",
        readonly=True,
        copy=False,
        index=True,
    )
    pino_handover_state = fields.Selection(
        [
            ("none", "Not Transferred"),
            ("pending", "Pending New Session"),
            ("done", "Transferred"),
        ],
        string="Session Handover Status",
        default="none",
        readonly=True,
        copy=False,
        index=True,
    )
    pino_handover_source = fields.Selection(
        [
            ("manual", "Manual POS Button"),
            ("closing", "POS Closing"),
            ("backend_button", "Backend Button"),
        ],
        string="Handover Source",
        readonly=True,
        copy=False,
        index=True,
    )
    pino_handover_from_session_id = fields.Many2one(
        "pos.session",
        string="Old Session",
        readonly=True,
        copy=False,
        index=True,
    )
    pino_handover_to_session_id = fields.Many2one(
        "pos.session",
        string="New Session",
        readonly=True,
        copy=False,
        index=True,
    )
    pino_old_session_cashier_id = fields.Many2one(
        "hr.employee",
        string="Old Session Cashier",
        readonly=True,
        copy=False,
        index=True,
    )
    pino_new_session_cashier_id = fields.Many2one(
        "hr.employee",
        string="New Session Cashier",
        readonly=True,
        copy=False,
        index=True,
    )
    pino_old_session_cashier_name = fields.Char(
        string="Old Session Cashier Name",
        readonly=True,
        copy=False,
    )
    pino_new_session_cashier_name = fields.Char(
        string="New Session Cashier Name",
        readonly=True,
        copy=False,
    )
    pino_handover_requested_at = fields.Datetime(
        string="Handover Requested At",
        readonly=True,
        copy=False,
    )
    pino_handover_completed_at = fields.Datetime(
        string="Handover Completed At",
        readonly=True,
        copy=False,
    )
    pino_handover_requested_by_id = fields.Many2one(
        "res.users",
        string="Handover Requested By",
        readonly=True,
        copy=False,
    )

    # ------------------------------------------------------------------
    # Opener / Closer (Foodics-like ownership of a table order)
    # ------------------------------------------------------------------
    # The *owner* of an open table order is simply its ``session_id``.
    # - Whoever creates the order is the opener (and the owner).
    # - Another cashier may add items: ownership does NOT change.
    # - Whoever takes money on the table (payment line or validation) becomes
    #   the closer and the order moves to his session, so the sale and the cash
    #   land in the drawer that actually received them.
    # - If the owner session is already closed/closing, the cashier touching
    #   the order takes it over.
    pino_owner_session_id = fields.Integer(
        string="Owner Session ID",
        compute="_compute_pino_owner_session_id",
        store=True,
        help="Plain integer copy of session_id, used by the POS UI to know who "
        "owns a table (the UI rewrites session_id locally on synced orders).",
    )
    pino_opened_session_id = fields.Many2one(
        "pos.session", string="Opened In Session", readonly=True, copy=False, index=True,
    )
    pino_opened_config_id = fields.Many2one(
        "pos.config", string="Opened In POS", readonly=True, copy=False, index=True,
    )
    pino_opened_employee_id = fields.Many2one(
        "hr.employee", string="Opened By", readonly=True, copy=False, index=True,
    )
    pino_opened_by_name = fields.Char(string="Opened By Name", readonly=True, copy=False)
    pino_opened_at = fields.Datetime(string="Opened At", readonly=True, copy=False)
    pino_closed_session_id = fields.Many2one(
        "pos.session", string="Closed In Session", readonly=True, copy=False, index=True,
    )
    pino_closed_config_id = fields.Many2one(
        "pos.config", string="Closed In POS", readonly=True, copy=False, index=True,
    )
    pino_closed_employee_id = fields.Many2one(
        "hr.employee", string="Closed By", readonly=True, copy=False, index=True,
    )
    pino_closed_by_name = fields.Char(string="Closed By Name", readonly=True, copy=False)
    pino_closed_at = fields.Datetime(string="Closed At", readonly=True, copy=False)
    pino_closed_by_other = fields.Boolean(
        string="Closed By Another Cashier",
        compute="_compute_pino_closed_by_other",
        store=True,
    )

    # ------------------------------------------------------------------
    # Bill printed (pre-payment check) -> table waits for payment
    # ------------------------------------------------------------------
    pino_bill_printed = fields.Boolean(string="Bill Printed", readonly=True, copy=False, index=True)
    pino_bill_print_count = fields.Integer(string="Bill Print Count", readonly=True, copy=False)
    pino_bill_printed_employee_id = fields.Many2one(
        "hr.employee", string="Bill Printed By", readonly=True, copy=False,
    )
    pino_bill_printed_by_name = fields.Char(string="Bill Printed By Name", readonly=True, copy=False)
    pino_bill_printed_session_id = fields.Many2one(
        "pos.session", string="Bill Printed In Session", readonly=True, copy=False,
    )
    pino_bill_printed_at = fields.Datetime(string="Bill Printed At", readonly=True, copy=False)

    def pino_mark_bill_printed(self, session_id, employee_id=False):
        """Called by the POS when a cashier prints the bill before payment."""
        session = self.env["pos.session"].browse(session_id).exists()
        employee = self.env["hr.employee"].sudo().browse(employee_id or []).exists()
        orders = self.filtered(lambda o: o.state == "draft")
        if not orders:
            return []
        name = self._pino_actor_name(employee, session) if session else (employee.name or self.env.user.name)
        now = fields.Datetime.now()
        for order in orders:
            order.with_context(pino_skip_handover_write=True).write({
                "pino_bill_printed": True,
                "pino_bill_print_count": order.pino_bill_print_count + 1,
                "pino_bill_printed_employee_id": employee.id or False,
                "pino_bill_printed_by_name": name,
                "pino_bill_printed_session_id": session.id or False,
                "pino_bill_printed_at": now,
            })
            order.message_post(body=_("Bill printed by %(cashier)s.", cashier=name))
        device = self.env.context.get("device_identifier", 0)
        for config in orders.config_id:
            config.notify_synchronisation(config.current_session_id.id, device)
        return orders.read([
            "pino_bill_printed",
            "pino_bill_print_count",
            "pino_bill_printed_by_name",
            "pino_bill_printed_at",
        ])

    # ------------------------------------------------------------------
    # Guest stay: from service start (table opened) to closing (payment)
    # ------------------------------------------------------------------
    pino_stay_duration = fields.Float(
        string="Guest Stay Duration",
        compute="_compute_pino_stay_duration",
        store=True,
        aggregator="avg",
        help="Time between opening the table and paying it, in hours.",
    )
    pino_stay_minutes = fields.Integer(
        string="Guest Stay (Minutes)",
        compute="_compute_pino_stay_duration",
        store=True,
        aggregator="avg",
    )
    pino_split_from_uuid = fields.Char(
        string="Split From Order",
        readonly=True,
        copy=False,
        help="UUID of the table order this one was split from (keeps the service start).",
    )

    @api.depends("pino_opened_at", "pino_closed_at", "table_id", "pino_split_from_uuid")
    def _compute_pino_stay_duration(self):
        for order in self:
            # Split bills are floating orders (no table) but belong to a table visit.
            is_table_service = order.table_id or order.pino_split_from_uuid
            if is_table_service and order.pino_opened_at and order.pino_closed_at:
                seconds = max((order.pino_closed_at - order.pino_opened_at).total_seconds(), 0)
                order.pino_stay_duration = seconds / 3600.0
                order.pino_stay_minutes = round(seconds / 60.0)
            else:
                order.pino_stay_duration = 0.0
                order.pino_stay_minutes = 0

    @api.depends("session_id")
    def _compute_pino_owner_session_id(self):
        for order in self:
            order.pino_owner_session_id = order.session_id.id or 0

    @api.depends("pino_opened_session_id", "pino_closed_session_id")
    def _compute_pino_closed_by_other(self):
        for order in self:
            order.pino_closed_by_other = bool(
                order.pino_opened_session_id
                and order.pino_closed_session_id
                and order.pino_opened_session_id != order.pino_closed_session_id
            )

    def _pino_context_employee(self):
        employee_id = self.env.context.get("pino_current_employee_id")
        if not employee_id:
            return self.env["hr.employee"]
        return self.env["hr.employee"].sudo().browse(employee_id).exists()

    def _pino_context_session(self):
        session_id = self.env.context.get("pino_current_session_id")
        if not session_id:
            return self.env["pos.session"]
        session = self.env["pos.session"].browse(session_id).exists()
        if session.state != "opened" or session.rescue:
            return self.env["pos.session"]
        return session

    @api.model
    def _pino_actor_name(self, employee, session):
        if employee:
            return employee.name
        if session.employee_id:
            return session.employee_id.name
        return session.user_id.name or self.env.user.name

    @api.model_create_multi
    def create(self, vals_list):
        now = fields.Datetime.now()
        Session = self.env["pos.session"]
        employee = self._pino_context_employee()
        for vals in vals_list:
            if vals.get("pino_opened_session_id") or not vals.get("session_id"):
                continue
            session = Session.browse(vals["session_id"])
            opener = employee or self.env["hr.employee"].browse(vals.get("employee_id") or [])
            vals.update({
                "pino_opened_session_id": session.id,
                "pino_opened_config_id": session.config_id.id,
                "pino_opened_employee_id": opener.id or False,
                "pino_opened_by_name": self._pino_actor_name(opener, session),
                "pino_opened_at": now,
            })
        return super().create(vals_list)

    def _pino_inherit_service_start(self, source_uuid):
        """A split bill keeps the service start and opener of the original table order."""
        self.ensure_one()
        source = self.search([("uuid", "=", source_uuid)], limit=1)
        if not source or source == self:
            return
        self.with_context(pino_skip_handover_write=True).write({
            "pino_split_from_uuid": source_uuid,
            "pino_opened_session_id": source.pino_opened_session_id.id,
            "pino_opened_config_id": source.pino_opened_config_id.id,
            "pino_opened_employee_id": source.pino_opened_employee_id.id,
            "pino_opened_by_name": source.pino_opened_by_name,
            "pino_opened_at": source.pino_opened_at or source.create_date,
        })

    @api.model
    def _pino_payment_commands(self, order_vals, existing_order):
        """Return (has_new_payment, kept_existing_payments) from the UI payload."""
        commands = order_vals.get("payment_ids") or []
        existing_by_uuid = {p.uuid: p for p in existing_order.payment_ids}
        removed_ids = set()
        has_new = False
        for command in commands:
            if not isinstance(command, (list, tuple)) or not command:
                continue
            if command[0] == Command.CREATE:
                vals = command[2] if len(command) > 2 and isinstance(command[2], dict) else {}
                if vals.get("uuid") not in existing_by_uuid:
                    has_new = True
            elif command[0] in (Command.DELETE, Command.UNLINK) and len(command) > 1:
                removed_ids.add(command[1])
        kept = existing_order.payment_ids.filtered(lambda p: p.id not in removed_ids)
        return has_new, kept

    @api.model
    def _pino_resolve_owner(self, order_vals, existing_order):
        """Decide which session must own ``existing_order`` after this sync.

        Returns ``(session, taking_over)``; ``session`` is empty when the
        standard behaviour must be kept.
        """
        current = self._pino_context_session()
        if not existing_order or not current or not existing_order.table_id:
            return self.env["pos.session"], False
        owner = existing_order.session_id
        if owner == current:
            return self.env["pos.session"], False

        has_new_payment, kept_payments = self._pino_payment_commands(order_vals, existing_order)
        finalizing = order_vals.get("state") in PINO_FINAL_STATES
        owner_gone = not owner or owner.state != "opened"

        if finalizing or has_new_payment or owner_gone:
            if kept_payments and not owner_gone:
                raise UserError(_(
                    "Table order %(order)s already has a payment taken by %(cashier)s. "
                    "Only that cashier can complete it (or remove the payment first).",
                    order=existing_order.name,
                    cashier=self._pino_actor_name(existing_order.employee_id, owner),
                ))
            return current, True
        # Another cashier only edited the table: the opener keeps it.
        return owner, False

    @api.model
    def _process_order(self, order, existing_order):
        # All pino_* fields are server-managed. The POS UI round-trips every
        # loaded field (often with stale/empty values when the related record
        # is unknown on that device), so never trust them from the payload.
        split_from_uuid = order.get("pino_split_from_uuid") if not existing_order else False
        for key in [key for key in order if key.startswith("pino_")]:
            order.pop(key)
        target, taking_over = self._pino_resolve_owner(order, existing_order)
        employee = self._pino_context_employee()
        if target:
            order["session_id"] = target.id
            if "config_id" in order:
                order["config_id"] = target.config_id.id
            if taking_over:
                if employee:
                    order["employee_id"] = employee.id
            elif "employee_id" in order:
                order["employee_id"] = existing_order.employee_id.id or False

        order_id = super()._process_order(order, existing_order)

        pos_order = self.browse(order_id)
        if split_from_uuid and pos_order and not existing_order:
            pos_order._pino_inherit_service_start(split_from_uuid)
        if taking_over and pos_order:
            pos_order.message_post(body=_(
                "Table order taken over by %(cashier)s (%(session)s).",
                cashier=self._pino_actor_name(employee, target),
                session=target.name,
            ))
        if pos_order and pos_order.state in PINO_FINAL_STATES and not pos_order.pino_closed_session_id:
            closer = employee or pos_order.employee_id
            pos_order.with_context(pino_skip_handover_write=True).write({
                "pino_closed_session_id": pos_order.session_id.id,
                "pino_closed_config_id": pos_order.config_id.id,
                "pino_closed_employee_id": closer.id or False,
                "pino_closed_by_name": self._pino_actor_name(closer, pos_order.session_id),
                "pino_closed_at": fields.Datetime.now(),
            })
        return order_id

    @api.model
    def _load_pos_data_domain(self, data, config):
        domain = super()._load_pos_data_domain(data, config)
        # ``_load_pos_data_search_read`` uses ``False`` to mean "load nothing".
        if domain is False or not config.module_pos_restaurant:
            return domain

        return Domain.OR([
            domain,
            [
                ("state", "=", "draft"),
                ("config_id", "=", config.id),
                ("table_id", "!=", False),
                ("pino_handover_state", "=", "pending"),
            ],
        ])

    @api.model
    def _load_pos_data_fields(self, config):
        fields_list = super()._load_pos_data_fields(config)
        # An empty list means "read every field", so there is nothing to extend.
        if not fields_list:
            return fields_list

        for field_name in (
            "pino_opened_at",
            "pino_split_from_uuid",
            "pino_bill_printed",
            "pino_bill_print_count",
            "pino_bill_printed_by_name",
            "pino_bill_printed_at",
            "pino_owner_session_id",
            "pino_opened_by_name",
            "pino_closed_by_name",
            "pino_is_session_transferred",
            "pino_handover_state",
            "pino_handover_source",
            "pino_handover_from_session_id",
            "pino_handover_to_session_id",
            "pino_old_session_cashier_id",
            "pino_new_session_cashier_id",
            "pino_old_session_cashier_name",
            "pino_new_session_cashier_name",
            "pino_handover_requested_at",
            "pino_handover_completed_at",
        ):
            if field_name not in fields_list:
                fields_list.append(field_name)
        return fields_list

    def write(self, vals):
        if self.env.context.get("pino_skip_handover_write") or "session_id" not in vals:
            return super().write(vals)

        new_session = self.env["pos.session"].browse(vals["session_id"])
        if not new_session:
            return super().write(vals)

        pending_orders = self.filtered(
            lambda order: order.pino_handover_state == "pending"
            and order.session_id.id != new_session.id
            and order.pino_handover_from_session_id.id != new_session.id
        )
        if not pending_orders:
            return super().write(vals)

        cashier_id, cashier_name = new_session._pino_get_cashier_snapshot()
        completed_vals = dict(vals)
        completed_vals.update({
            "pino_is_session_transferred": True,
            "pino_handover_state": "done",
            "pino_handover_to_session_id": new_session.id,
            "pino_new_session_cashier_id": cashier_id,
            "pino_new_session_cashier_name": cashier_name,
            "pino_handover_completed_at": fields.Datetime.now(),
        })

        other_orders = self - pending_orders
        result = True
        if other_orders:
            result = super(PosOrder, other_orders).write(vals) and result
        if pending_orders:
            result = super(PosOrder, pending_orders).write(completed_vals) and result
        return result
