"""Durable, per-printer delivery to an online POS browser, not to the web server."""
import json
import secrets

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


def _note_text(value):
    if not value:
        return ""
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        return str(value)
    if isinstance(parsed, list):
        return ", ".join(str(item.get("text", "")) if isinstance(item, dict) else str(item) for item in parsed)
    return str(value)


class BlendKitchenPrint(models.Model):
    _name = "blend.kitchen.print"
    _description = "Blend Kitchen Printing"
    _order = "id"

    order_id = fields.Many2one("pos.order", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="order_id.company_id", store=True, index=True)
    printer_id = fields.Many2one("pos.printer", required=True, ondelete="restrict")
    state = fields.Selection([
        ("pending", "Waiting for POS"), ("printing", "Printing / confirmation pending"),
        ("done", "Printed"), ("failed", "Print failed"),
    ], default="pending", required=True, index=True)
    receipt = fields.Json(required=True, groups="base.group_system")
    claim_token = fields.Char(groups="base.group_system", copy=False)
    claimed_at = fields.Datetime(readonly=True)
    printed_at = fields.Datetime(readonly=True)
    error = fields.Text(readonly=True)
    attempts = fields.Integer(readonly=True)
    _order_printer_unique = models.Constraint("UNIQUE(order_id, printer_id)", "This order already has a job for this printer.")

    def _check_operator(self):
        if not (self.env.user.has_group("point_of_sale.group_pos_user") or self.env.user.has_group("base.group_system")):
            raise AccessError(_("Only POS users may process Blend kitchen printing."))
        self.check_access("read")
        if any(job.company_id not in self.env.companies for job in self):
            raise AccessError(_("Select the order company before processing kitchen printing."))

    @api.model
    def claim_next(self, config_id, session_id, printer_ids):
        self._check_operator()
        config = self.env["pos.config"].browse(config_id).exists()
        session = self.env["pos.session"].browse(session_id).exists()
        config.check_access("read")
        session.check_access("read")
        if (not config or not session or session.config_id != config
                or config.company_id not in self.env.companies):
            raise AccessError(_("Invalid POS session for Blend printing."))
        if not config.blend_enabled or session.state != "opened":
            return False
        printers = config.printer_ids.filtered(lambda printer: printer.id in printer_ids)
        job = self.search([
            ("order_id.session_id", "=", session.id), ("printer_id", "in", printers.ids),
            ("state", "=", "pending"), ("order_id.blend_partner_reference", "!=", False),
            ("order_id.state", "!=", "cancel"), ("order_id.blend_refund_required", "=", False),
            ("order_id.blend_status", "not in", ["cancelled", "rejected", "time_out"]),
        ], limit=1)
        if not job:
            return False
        # Concurrent transactions either retry or observe the claimed state.
        job.lock_for_update()
        job.invalidate_recordset()
        if job.state != "pending":
            return False
        try:
            receipt = job.sudo()._receipt_for_printing()
        except UserError as error:
            # A stale legacy snapshot must not block other pending kitchen jobs.
            job.sudo().write({"state": "failed", "error": str(error)})
            config._notify("BLEND_KITCHEN_PRINT", {
                "warning": str(error), "reference": job.order_id.blend_partner_reference,
            })
            return False
        token = secrets.token_urlsafe(32)
        job.sudo().write({"state": "printing", "claim_token": token,
                          "claimed_at": fields.Datetime.now(), "attempts": job.attempts + 1, "error": False})
        return {"id": job.id, "token": token, "printer_id": job.printer_id.id,
                "reference": job.order_id.blend_partner_reference, "receipt": receipt}

    def _receipt_for_printing(self):
        """Upgrade only matching pending snapshots; never requeue a printed job."""
        self.ensure_one()
        if self.receipt.get("blend_kitchen", {}).get("version") == 2:
            return self.receipt
        order = self.order_id
        lines = order._blend_kitchen_lines(self.printer_id)
        receipt = order._blend_kitchen_receipt(lines)
        keys = ("quantity", "basic_name", "attribute_value_names", "note", "customer_note")
        original_lines = self.receipt.get("changes", {}).get("data", [])
        rebuilt_lines = [{key: line[key] for key in keys} for line in receipt["changes"]["data"]]
        if (original_lines != rebuilt_lines
                or self.receipt.get("pos_reference") != order.blend_partner_reference):
            raise UserError(_(
                "The saved kitchen ticket for %s no longer matches the order or printer routing. "
                "Review the pending ticket before printing; its contents were not replaced.",
                order.blend_partner_reference,
            ))
        # Retain the original header and notes; add presentation/identity only.
        receipt["blend_kitchen"]["order_time"] = self.receipt.get("time") or receipt["time"]
        receipt = {**self.receipt, "blend_kitchen": receipt["blend_kitchen"],
                   "tracking_number": receipt["tracking_number"],
                   "changes": {**self.receipt["changes"], "data": receipt["changes"]["data"]}}
        self.receipt = receipt
        return receipt

    def finish(self, token, successful, error=""):
        self.ensure_one()
        self._check_operator()
        self.lock_for_update()
        self.invalidate_recordset()
        if not token or not secrets.compare_digest(str(token), self.sudo().claim_token or ""):
            raise AccessError(_("Invalid kitchen print confirmation."))
        if self.state in ("done", "failed"):
            return True  # An acknowledgement retry never reprints the receipt.
        if self.state != "printing" or type(successful) is not bool:
            raise UserError(_("This kitchen print job is not awaiting confirmation."))
        self.sudo().write({"state": "done" if successful else "failed",
                          "printed_at": fields.Datetime.now() if successful else False,
                          "error": False if successful else str(error or "Printer did not confirm success")[:2000]})
        return True

    def action_retry(self):
        self.ensure_one()
        self._check_operator()
        self.lock_for_update()
        self.invalidate_recordset()
        if self.state not in ("failed", "printing"):
            raise UserError(_("Only failed or unconfirmed print jobs can be retried."))
        if self.state == "printing" and not self.env.user.has_group("point_of_sale.group_pos_manager"):
            if not self.env.user.has_group("base.group_system"):
                raise AccessError(_("A manager must review unconfirmed printing before retrying."))
        self.sudo().write({"state": "pending", "claim_token": False, "error": False})
        self.order_id.config_id._notify("BLEND_KITCHEN_PRINT", {})
        return True


class PosOrder(models.Model):
    _inherit = "pos.order"

    blend_kitchen_print_ids = fields.One2many("blend.kitchen.print", "order_id", readonly=True)
    blend_kitchen_print_warning = fields.Text(string="Kitchen Printer Routing Warning", readonly=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and (any("blend_kitchen_print_warning" in vals for vals in vals_list)
                                or "default_blend_kitchen_print_warning" in self.env.context):
            raise AccessError(_("Kitchen printing warnings are managed by the integration."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and "blend_kitchen_print_warning" in vals:
            raise AccessError(_("Kitchen printing warnings are managed by the integration."))
        return super().write(vals)

    @api.model
    def _load_pos_data_fields(self, config):
        # Queue payloads and tokens are fetched only via the authorized claim RPC.
        names = super()._load_pos_data_fields(config) or self.fields_get(attributes=["type"])
        return [name for name in names if name not in ("blend_kitchen_print_ids", "blend_kitchen_print_warning")]

    def _blend_kitchen_lines(self, printer):
        self.ensure_one()
        line_categories = {}
        for line in self.lines:
            categories = line.product_id.pos_categ_ids
            parents = categories.parent_id
            while parents:
                categories |= parents
                parents = parents.parent_id - categories
            line_categories[line.id] = categories.ids
        category_ids = set(printer.product_categories_ids.ids)
        lines = self.lines.filtered(lambda line: line.qty > 0 and not line.blend_is_discount_line
                                   and bool(category_ids.intersection(line_categories[line.id])))
        # Keep the existing routing: uncategorized modifiers follow their parent.
        lines |= self.lines.filtered(lambda line: line.qty > 0 and not line.blend_is_discount_line and not line.product_id.pos_categ_ids
                                     and str(getattr(line, "parent_line_id", "")) in {str(item.id) for item in lines})
        return lines.sorted("id")

    def _blend_kitchen_receipt(self, lines):
        self.ensure_one()
        receipt_lines = []
        by_reference = {str(line.id): line.uuid for line in self.lines}
        by_reference.update({line.uuid: line.uuid for line in self.lines})
        for line in lines:
            product = line.product_id
            arabic = getattr(product, "kitchen_name_ar", False) or product.name
            english = getattr(product, "kitchen_name_en", False) or product.name
            parent = (getattr(line, "modifier_parent_uuid", False)
                      or by_reference.get(str(getattr(line, "parent_line_id", "")), ""))
            receipt_lines.append({
                "uuid": line.uuid, "product_id": product.id,
                "quantity": line.qty, "basic_name": product.name,
                "product_name_ar": arabic, "product_name_en": english,
                "pino_latin": getattr(product.product_tmpl_id, "pino_name_latin", False) or english,
                "isModifierChild": bool(parent), "modifier_parent_uuid": parent,
                "combo_parent_uuid": line.combo_parent_id.uuid or "",
                "attribute_value_names": line.attribute_value_ids.mapped("name"),
                "note": _note_text(line.note), "customer_note": line.customer_note or "",
                "blend_line_customer_note": line.customer_note or "",
            })
        notes = "\n".join(filter(None, [_note_text(self.internal_note), self.general_customer_note]))
        if notes and receipt_lines:
            receipt_lines[0]["customer_note"] = "\n".join(filter(None, [receipt_lines[0]["customer_note"], notes]))
        order_time = fields.Datetime.context_timestamp(self, self.date_order).strftime("%H:%M")
        return {
            "pos_reference": self.blend_partner_reference,
            "config_name": self.config_id.name, "employee_name": "Blend",
            "time": order_time, "tracking_number": self.tracking_number,
            "preset_name": self.blend_order_type or "Blend", "reprint": False,
            "blend_kitchen": {"version": 2, "reference": self.blend_partner_reference,
                              "order_type": self.blend_order_type, "order_time": order_time,
                              "general_note": notes,
                              "table_number": self.table_id.table_number or ""},
            "changes": {"title": "Blend", "data": receipt_lines},
        }

    def _blend_queue_kitchen_print(self):
        self.ensure_one()
        if not self.blend_partner_reference or self.state == "cancel":
            return
        self.lock_for_update()
        self.invalidate_recordset(["blend_kitchen_print_ids"])
        queued = self.blend_kitchen_print_ids.printer_id
        routed = self.env["pos.order.line"]
        for printer in self.config_id.printer_ids:
            lines = self._blend_kitchen_lines(printer)
            if not lines:
                continue
            routed |= lines
            if printer in queued:
                continue
            self.env["blend.kitchen.print"].sudo().create({
                "order_id": self.id, "printer_id": printer.id,
                "receipt": self._blend_kitchen_receipt(lines),
            })
        missing = self.lines.filtered(lambda line: line.qty > 0 and not line.blend_is_discount_line) - routed
        warning = _("No kitchen printer is configured for: %s. Check the POS printer categories.",
                    ", ".join(missing.product_id.mapped("display_name"))) if missing else False
        self.sudo().blend_kitchen_print_warning = warning
        if self.blend_kitchen_print_ids or warning:
            self.config_id._notify("BLEND_KITCHEN_PRINT", {"warning": warning, "reference": self.blend_partner_reference})
