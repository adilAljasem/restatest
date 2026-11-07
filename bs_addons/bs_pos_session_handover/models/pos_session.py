# -*- coding: utf-8 -*-

from markupsafe import Markup

from odoo import _, fields, models
from odoo.exceptions import UserError


PINO_HANDOVER_SOURCES = {"manual", "closing", "backend_button"}


class PosSession(models.Model):
    _inherit = "pos.session"

    pino_handover_order_count = fields.Integer(
        string="Transferred Table Orders",
        compute="_compute_pino_handover_order_count",
    )
    pino_last_claimed_handover_count = fields.Integer(
        string="Last Claimed Table Orders",
        readonly=True,
        copy=False,
    )
    pino_last_claimed_handover_at = fields.Datetime(
        string="Last Claimed Table Orders At",
        readonly=True,
        copy=False,
    )

    def _compute_pino_handover_order_count(self):
        PosOrder = self.env["pos.order"]
        for session in self:
            session.pino_handover_order_count = PosOrder.search_count([
                "|",
                ("pino_handover_from_session_id", "=", session.id),
                ("pino_handover_to_session_id", "=", session.id),
            ])

    def _pino_get_cashier_snapshot(self):
        self.ensure_one()
        cashier = self.employee_id
        return cashier.id if cashier else False, cashier.name if cashier else self.user_id.name

    def get_session_orders(self):
        orders = super().get_session_orders()
        return orders.filtered(lambda order: order.pino_handover_state != "pending")

    def _set_opening_control_data(self, cashbox_value, notes):
        result = super()._set_opening_control_data(cashbox_value, notes)
        sessions_to_claim = self.filtered(lambda session: session.config_id.module_pos_restaurant and not session.rescue)
        if sessions_to_claim:
            claim_result = sessions_to_claim.pino_claim_pending_table_handovers()
            counts_by_session = claim_result.get("counts_by_session", {})
            now = fields.Datetime.now()
            for session in sessions_to_claim:
                claimed_count = counts_by_session.get(session.id, 0)
                if claimed_count:
                    session.write({
                        "pino_last_claimed_handover_count": claimed_count,
                        "pino_last_claimed_handover_at": now,
                    })
        return result

    def _pino_handover_target_sessions(self):
        """Open sessions of the other POS sharing at least one floor with this one."""
        self.ensure_one()
        configs = (self.config_id.floor_ids.pos_config_ids - self.config_id).filtered(
            lambda c: c.module_pos_restaurant and c.company_id == self.config_id.company_id
        )
        if not configs:
            return self.browse()
        return self.search([
            ("config_id", "in", configs.ids),
            ("state", "=", "opened"),
            ("rescue", "=", False),
            ("id", "!=", self.id),
        ])

    def pino_get_handover_targets(self):
        self.ensure_one()
        return [
            {
                "id": session.id,
                "name": session.name,
                "config_name": session.config_id.name,
                "cashier_name": session._pino_get_cashier_snapshot()[1] or "",
            }
            for session in self._pino_handover_target_sessions()
        ]

    def _pino_handover_to_session(self, orders, target, source):
        """Give the orders directly to another open cashier (no pending step)."""
        self.ensure_one()
        cashier_id, cashier_name = self._pino_get_cashier_snapshot()
        new_cashier_id, new_cashier_name = target._pino_get_cashier_snapshot()
        now = fields.Datetime.now()
        vals = {
            "session_id": target.id,
            "pino_is_session_transferred": True,
            "pino_handover_state": "done",
            "pino_handover_source": source,
            "pino_handover_from_session_id": self.id,
            "pino_handover_to_session_id": target.id,
            "pino_old_session_cashier_id": cashier_id,
            "pino_new_session_cashier_id": new_cashier_id,
            "pino_old_session_cashier_name": cashier_name,
            "pino_new_session_cashier_name": new_cashier_name,
            "pino_handover_requested_at": now,
            "pino_handover_completed_at": now,
            "pino_handover_requested_by_id": self.env.user.id,
        }
        if new_cashier_id:
            vals["employee_id"] = new_cashier_id
        orders.with_context(pino_skip_handover_write=True).write(vals)
        body = Markup("<p>%s</p>") % _(
            "%(count)s table order(s) handed over from %(old)s to %(new)s (%(session)s).",
            count=len(orders),
            old=cashier_name,
            new=new_cashier_name,
            session=target.name,
        )
        self.message_post(body=body)
        target.message_post(body=body)
        for order in orders:
            order.message_post(body=body)
        # Refresh every POS sharing the floor (the target and its trusted POS).
        target.config_id.notify_synchronisation(target.id, 0)
        return {
            "successful": True,
            "count": len(orders),
            "order_ids": orders.ids,
            "source": source,
            "target_session_id": target.id,
            "message": _(
                "%(count)s table order(s) handed over to %(cashier)s.",
                count=len(orders),
                cashier=new_cashier_name,
            ),
        }

    def pino_prepare_table_handover(self, order_ids=None, source="backend_button", target_session_id=None):
        self.ensure_one()
        if source not in PINO_HANDOVER_SOURCES:
            raise UserError(_("Unsupported table handover source."))

        if not self.config_id.module_pos_restaurant:
            return {
                "successful": True,
                "count": 0,
                "order_ids": [],
                "message": _("This POS is not configured as a restaurant."),
            }
        if self.state == "closed":
            raise UserError(_("You cannot transfer table orders from a closed POS session."))

        domain = [
            ("session_id", "=", self.id),
            ("state", "=", "draft"),
            ("table_id", "!=", False),
            ("lines", "!=", False),
        ]
        selected_orders = self.env["pos.order"]
        if order_ids is not None:
            selected_orders = selected_orders.browse(order_ids).exists()
            domain.append(("id", "in", selected_orders.ids))

        orders = self.env["pos.order"].search(domain)
        if selected_orders and set(selected_orders.ids) != set(orders.ids):
            raise UserError(_("Only unpaid draft table orders from this session can be transferred."))

        paid_orders = orders.filtered("payment_ids")
        if paid_orders:
            raise UserError(_(
                "Cannot transfer table orders that already have payment lines: %s",
                ", ".join(paid_orders.mapped("name")),
            ))

        if not orders:
            return {
                "successful": True,
                "count": 0,
                "order_ids": [],
                "message": _("No unpaid draft table orders need a session handover."),
            }

        if target_session_id:
            target = self._pino_handover_target_sessions().filtered(lambda s: s.id == target_session_id)
            if not target:
                raise UserError(_(
                    "The selected cashier session is no longer open or does not share a floor with this POS."
                ))
            return self._pino_handover_to_session(orders, target, source)

        cashier_id, cashier_name = self._pino_get_cashier_snapshot()
        now = fields.Datetime.now()
        orders.with_context(pino_skip_handover_write=True).write({
            "pino_is_session_transferred": True,
            "pino_handover_state": "pending",
            "pino_handover_source": source,
            "pino_handover_from_session_id": self.id,
            "pino_handover_to_session_id": False,
            "pino_old_session_cashier_id": cashier_id,
            "pino_new_session_cashier_id": False,
            "pino_old_session_cashier_name": cashier_name,
            "pino_new_session_cashier_name": False,
            "pino_handover_requested_at": now,
            "pino_handover_completed_at": False,
            "pino_handover_requested_by_id": self.env.user.id,
        })
        source_label = dict(self.env["pos.order"]._fields["pino_handover_source"].selection).get(source, source)
        body = Markup("<p>%s</p><p>%s</p>") % (
            _(
                "Prepared %s unpaid table order(s) for transfer to the next POS session.",
                len(orders),
            ),
            _("Source: %s", source_label),
        )
        self.message_post(body=body)
        for order in orders:
            order.message_post(body=body)

        return {
            "successful": True,
            "count": len(orders),
            "order_ids": orders.ids,
            "source": source,
            "message": _("Prepared %s unpaid table order(s) for transfer.", len(orders)),
        }

    def action_pino_prepare_table_handover(self):
        self.ensure_one()
        result = self.pino_prepare_table_handover(source="backend_button")
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success" if result["successful"] else "warning",
                "title": _("Table Handover"),
                "message": result["message"],
                "sticky": False,
            },
        }

    def pino_claim_pending_table_handovers(self):
        claimed_by_session = {}
        for session in self:
            if not session.config_id.module_pos_restaurant or session.rescue or session.state == "closed":
                claimed_by_session[session.id] = self.env["pos.order"]
                continue

            orders = self.env["pos.order"].search([
                ("config_id", "=", session.config_id.id),
                ("state", "=", "draft"),
                ("table_id", "!=", False),
                ("pino_handover_state", "=", "pending"),
                ("session_id", "!=", session.id),
            ])
            if orders:
                write_vals = {"session_id": session.id}
                if session.employee_id:
                    write_vals["employee_id"] = session.employee_id.id
                orders.write(write_vals)
                body = Markup("<p>%s</p>") % _(
                    "Received %s unpaid table order(s) from a previous POS session.",
                    len(orders),
                )
                session.message_post(body=body)
                for order in orders:
                    order.message_post(body=body)
            claimed_by_session[session.id] = orders
        return {
            "successful": True,
            "count": sum(len(orders) for orders in claimed_by_session.values()),
            "counts_by_session": {
                session_id: len(orders)
                for session_id, orders in claimed_by_session.items()
            },
            "order_ids": [order_id for orders in claimed_by_session.values() for order_id in orders.ids],
        }

    def pino_get_handover_opening_notification(self):
        self.ensure_one()
        count = self.pino_last_claimed_handover_count
        orders = self.env["pos.order"].search([
            ("session_id", "=", self.id),
            ("pino_handover_to_session_id", "=", self.id),
            ("pino_handover_state", "=", "done"),
        ]) if count else self.env["pos.order"]
        if count:
            self.write({"pino_last_claimed_handover_count": 0})
        return {
            "count": count,
            "order_ids": orders.ids,
        }

    def action_pino_view_handover_orders(self):
        self.ensure_one()
        return {
            "name": _("Transferred Table Orders"),
            "type": "ir.actions.act_window",
            "res_model": "pos.order",
            "view_mode": "list,form",
            "domain": [
                "|",
                ("pino_handover_from_session_id", "=", self.id),
                ("pino_handover_to_session_id", "=", self.id),
            ],
            "context": {"search_default_pino_transferred": 1},
        }
