from odoo import Command, _, api, fields, models
from odoo.exceptions import ValidationError


BLEND_STATUSES = [
    ("pending", "Pending"),
    ("accepted", "Accepted"),
    ("preparing", "Preparing"),
    ("prepared", "Prepared"),
    ("rejected", "Rejected"),
    ("cancelled", "Cancelled"),
    ("time_out", "Timed Out"),
    ("assigned", "Driver Assigned"),
    ("at_pick_up", "Driver at Pickup"),
    ("on_the_way", "On the Way"),
    ("delivered", "Delivered"),
]


class PosOrder(models.Model):
    _inherit = "pos.order"

    def _get_invoice_lines_values(self, line_values, pos_line, move_type):
        values = super()._get_invoice_lines_values(line_values, pos_line, move_type)
        if (not self.blend_order_id or pos_line.product_id.type != "combo"
                or self.currency_id.is_zero(pos_line.price_subtotal_incl)):
            return values
        # Odoo invoices regular combo parents as section headings because their
        # selected children carry the price. Blend can send a priced parent with
        # no selections, so preserve that POS amount on the invoice.
        values.pop("display_type", None)
        values.update({
            "product_id": line_values["product_id"].id,
            "discount": line_values["discount"],
            "price_unit": line_values["price_unit"],
            "name": line_values["name"],
            "tax_ids": [Command.set(line_values["tax_ids"].ids)],
            "extra_tax_data": self.env["account.tax"]._export_base_line_extra_tax_data(
                line_values,
            ),
        })
        return values

    blend_order_id = fields.Char(
        string="Blend Order ID", copy=False, index=True, readonly=True
    )
    blend_partner_reference = fields.Char(
        string="Blend Partner Reference", copy=False, index=True, readonly=True
    )
    blend_order_type = fields.Char(
        string="Blend Order Type", copy=False, readonly=True
    )
    blend_aggregator = fields.Char(
        string="Blend Aggregator", copy=False, readonly=True
    )
    blend_aggregator_order_id = fields.Char(
        string="Aggregator Order ID", copy=False, index=True, readonly=True
    )
    blend_schedule_at = fields.Char(
        string="Scheduled For (Blend local time)", copy=False, readonly=True, index=True
    )
    blend_payload = fields.Json(string="Blend Payload", copy=False, readonly=True)
    blend_status = fields.Selection(
        BLEND_STATUSES, string="Blend Status", copy=False, tracking=True
    )
    blend_delivery_status = fields.Selection(
        [
            ("assigned", "Driver Assigned"),
            ("at_pick_up", "Driver at Pickup"),
            ("on_the_way", "On the Way"),
            ("cancelled", "Cancelled"),
            ("delivered", "Delivered"),
        ],
        string="Delivery Status to Blend",
        copy=False,
    )
    blend_car_color = fields.Char(string="Car Color", copy=False)
    blend_car_plate_number = fields.Char(string="Car Plate Number", copy=False)
    blend_car_plate_letters = fields.Char(string="Car Plate Letters", copy=False)
    blend_car_brand = fields.Char(string="Car Brand", copy=False)

    @api.model
    def _load_pos_preparation_data_fields(self):
        return [*super()._load_pos_preparation_data_fields(), "blend_aggregator"]

    def write(self, vals):
        result = super().write(vals)
        if (
            not self.env.context.get("bs_blend_bridge_sync")
            and {"blend_order_id", "blend_status"}.intersection(vals)
        ):
            bridge = self.env["bs.delivery.blend.bridge"]
            for order in self:
                bridge.sync_order(order)
        return result

    def _process_saved_order(self, draft):
        # Blend is an inbound source of platform orders. POS cashier orders
        # have no Blend identity and must never be exported as new orders.
        return super()._process_saved_order(draft)

    def action_pos_order_cancel(self):
        result = super().action_pos_order_cancel()
        for order in self:
            if not order._blend_core_order():
                continue
            connection = order.config_id._blend_connection(connected=True)
            if connection:
                order._blend_enqueue_status("cancelled", connection=connection)
        return result

    def _blend_payment_method(self):
        names = list(dict.fromkeys(self.payment_ids.mapped("payment_method_id.name")))
        return ", ".join(names) or "other"

    def _blend_order_type(self):
        candidates = []
        if "pos_order_type_name" in self._fields:
            candidates.append(self["pos_order_type_name"] or "")
        candidates.append(self.preset_id.name or "")
        text = " ".join(candidates).strip().lower()
        if any(token in text for token in ("delivery", "توصيل")):
            return "DELIVERY"
        if any(
            token in text
            for token in ("pickup", "pick-up", "takeaway", "take away", "سفري", "استلام")
        ):
            return "PICK-UP"
        return "DINE-IN"

    def _blend_customer_payload(self):
        partner = self.partner_id
        if not partner:
            return None
        address = ", ".join(
            value
            for value in (
                partner.street,
                partner.street2,
                partner.city,
                partner.state_id.name,
                partner.country_id.name,
            )
            if value
        ) or None
        return {
            "name": partner.name or "",
            "phone": self.mobile or partner.mobile or partner.phone or "",
            "address": address,
        }

    def _blend_car_payload(self):
        values = {
            "color": self.blend_car_color or "",
            "plate_number": self.blend_car_plate_number or "",
            "plate_letters": self.blend_car_plate_letters or "",
            "car_brand": self.blend_car_brand or "",
        }
        return values if any(values.values()) else None

    def _blend_product_external_id(self, product_template, connection):
        binding = self.env["bs.delivery.product.binding"].sudo().search(
            [
                ("connection_id", "=", connection.id),
                ("product_id", "=", product_template.product_variant_id.id),
                ("active", "=", True),
            ],
            limit=1,
        )
        external_id = (
            binding.external_product_id or product_template.blend_external_id or ""
        ).strip()
        if not external_id:
            raise ValidationError(
                _("Product '%s' has no Blend ID.") % product_template.display_name
            )
        return external_id

    def _blend_build_items(self, connection):
        self.ensure_one()
        normal_lines = self.lines.filtered(
            lambda line: not line.combo_parent_id and not line.modifier_parent_line_id
        )
        modifier_lines = self.lines - normal_lines
        items = []
        for line in normal_lines:
            options = []
            base_price = line.price_unit
            for option_line in modifier_lines.filtered(
                lambda candidate: candidate.combo_parent_id == line
                or candidate.modifier_parent_line_id == line
            ):
                if option_line.product_id.product_tmpl_id.blend_is_base:
                    base_price += option_line.price_unit
                    continue
                options.append(
                    {
                        "id": self._blend_product_external_id(
                            option_line.product_id.product_tmpl_id, connection
                        ),
                        "price": option_line.price_unit,
                        "quantity": option_line.modifier_qty_per_parent or abs(option_line.qty),
                    }
                )
            items.append(
                {
                    "id": self._blend_product_external_id(
                        line.product_id.product_tmpl_id, connection
                    ),
                    "quantity": abs(line.qty),
                    "price": base_price,
                    "note": line.customer_note or "",
                    "options": options,
                }
            )
        if not items:
            raise ValidationError(_("The POS order has no Blend order items."))
        return items

    def _blend_build_order_created_payload(self, connection=None):
        self.ensure_one()
        connection = connection or self.config_id._blend_connection()
        if not connection:
            raise ValidationError(_("The POS branch has no Blend connection."))
        store = connection._blend_store_for_config(self.config_id)
        if not store:
            raise ValidationError(_("The POS branch has no Blend store binding."))
        currency = self.currency_id
        gross_subtotal = sum(abs(line.qty) * line.price_unit for line in self.lines)
        discount = sum(
            abs(line.qty) * line.price_unit * (line.discount or 0.0) / 100.0
            for line in self.lines
        )
        schedule_date = fields.Datetime.to_string(self.preset_time) if self.preset_time else None
        return {
            "event": "order_created",
            "event_time": fields.Datetime.to_string(
                self.date_order or fields.Datetime.now()
            ),
            "branch_id": store.remote_store_id,
            "payload": {
                "id": self.uuid or self.pos_reference or str(self.id),
                "payment_method": self._blend_payment_method(),
                "order_type": self._blend_order_type(),
                "note": self.general_customer_note or "",
                "subtotal": currency.round(gross_subtotal),
                "tax": currency.round(self.amount_tax),
                "discount": currency.round(discount),
                "total": currency.round(self.amount_total),
                "is_schedule": 1 if schedule_date else 0,
                "schedule_date": schedule_date,
                "car_data": self._blend_car_payload(),
                "items": self._blend_build_items(connection),
                "customer": self._blend_customer_payload(),
            },
        }

    def _blend_enqueue_order_created(self, *, connection=None):
        raise ValidationError(_("POS orders cannot be exported as Blend orders."))

    def _blend_status_payload(self, status, connection=None):
        self.ensure_one()
        connection = connection or self.config_id._blend_connection()
        if not connection:
            raise ValidationError(_("The POS branch has no Blend connection."))
        store = connection._blend_store_for_config(self.config_id)
        if not store:
            raise ValidationError(_("The POS branch has no Blend store binding."))
        event_time = fields.Datetime.to_string(fields.Datetime.now())
        order_reference = self.blend_order_id or self.uuid or self.pos_reference or str(self.id)
        if connection.blend_schema == "pos":
            status_map = {
                "cancelled": "CANCELED",
                "accepted": "ACCEPTED",
                "preparing": "PREPARING",
                "rejected": "REJECTED",
                "time_out": "TIME_OUT",
            }
            return {
                "brand_id": store.remote_vendor_id or connection.blend_brand_id or "",
                "event_time": event_time,
                "event": "order.updated",
                "branch_id": store.remote_store_id,
                "order_id": order_reference,
                "status": status_map.get(status, str(status).upper()),
            }
        return {
            "event": "order_updated",
            "event_time": event_time,
            "branch_id": store.remote_store_id,
            "payload": {"order_id": order_reference, "status": status},
        }

    def _blend_enqueue_status(self, status, *, connection=None):
        for order in self:
            canonical = order._blend_core_order()
            if not canonical:
                raise ValidationError(_("Only Blend orders received through Delivery Core can send a Blend status."))
            resolved = connection or order.config_id._blend_connection(connected=True)
            if not resolved:
                continue
            if canonical.connection_id != resolved:
                raise ValidationError(_("The Blend status connection does not own this order."))
            binding = canonical.binding_id
            payload = order._blend_status_payload(status, resolved)
            self.env["bs.delivery.outbox"].sudo().enqueue(
                resolved,
                "update_order",
                binding,
                status_value=status,
                idempotency_key="blend-%s:%s:%s:%s"
                % (
                    payload["event"],
                    resolved.id,
                    order.uuid or order.id,
                    payload["event_time"],
                ),
            )
        return True

    def _blend_core_order(self):
        self.ensure_one()
        if not self.blend_order_id:
            return self.env["bs.delivery.order"]
        return self.env["bs.delivery.order"].sudo().search([
            ("pos_order_id", "=", self.id),
            ("contract_version", "=", 1),
            ("provider_id.code", "=", "blend"),
            ("blend_api_order_id", "=", self.blend_order_id),
        ], limit=1)

    def action_blend_send_delivery_status(self):
        for order in self:
            if not order.blend_delivery_status:
                raise ValidationError(
                    _("Select a delivery status before sending it to Blend.")
                )
            order._blend_enqueue_status(order.blend_delivery_status)
        return True

    def action_blend_retry(self):
        jobs = self.env["bs.delivery.outbox"].sudo().search(
            [
                ("provider_id.code", "=", "blend"),
                ("binding_id.pos_order_id", "in", self.ids),
                ("state", "in", ("failed", "retry")),
            ]
        )
        failed = jobs.filtered(lambda job: job.state == "failed")
        waiting = jobs - failed
        if failed:
            failed.action_retry()
        if waiting:
            waiting.write({"next_attempt_at": fields.Datetime.now()})
            waiting._trigger_processing()
        return True


class PosOrderLine(models.Model):
    _inherit = "pos.order.line"

    def _expected_modifier_unit_price(self):
        if (self.env.context.get("bs_delivery_core_import")
                and self.is_modifier_line
                and self.order_id.blend_order_id
                and self.order_id._blend_core_order()):
            # Core built this unit price from Blend's validated line total.
            # Keep Bunbastic's quantity, option, tax, and discount checks.
            return self.price_unit
        return super()._expected_modifier_unit_price()

    def _prepare_base_line_for_taxes_computation(self):
        values = super()._prepare_base_line_for_taxes_computation()
        if (self.order_id.blend_order_id
                and self.product_id.default_code == "BLEND-DISCOUNT"):
            # A promotion reduces a sale; it is not a refund transaction.
            values["is_refund"] = False
        return values

