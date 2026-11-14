"""Bridge POS -> kitchen screens.

The POS keeps, on each order, the state it last sent to the kitchen
(`last_order_preparation_change`). Every time that state changes on the server,
or the order is paid, or a self-order arrives, the dispatcher compares what each
kitchen screen already received (per order line) with what it should have, and
creates a ticket for the difference (or cancels quantities that were removed).
It is idempotent: running it twice never sends a dish twice.
"""
import json
import logging
from collections import defaultdict

from odoo import _, api, fields, models

from .kds_ticket import note_text

_logger = logging.getLogger(__name__)

PAID_STATES = ("paid", "done", "invoiced")


def _modifier_delta(old, new):
    """New modifier list with a status on what changed since the kitchen last saw it."""
    old_by_key = {m.get("key"): m for m in old}
    new_keys = {m.get("key") for m in new}
    out = []
    for m in new:
        before = old_by_key.get(m.get("key"))
        status = "added" if before is None else ("updated" if before != m else None)
        out.append(dict(m, status=status))
    out += [dict(m, status="removed") for m in old if m.get("key") not in new_keys]
    return out


class PosOrder(models.Model):
    _inherit = "pos.order"

    resta_kds_ticket_ids = fields.One2many("resta.kds.ticket", "pos_order_id", "تذاكر المطبخ")
    resta_kds_status = fields.Selection([
        ("none", "لم يُرسل للمطبخ"), ("preparing", "قيد التحضير"), ("ready", "جاهز"),
        ("served", "تم التسليم"),  # set by integrations (e.g. delivery handed over), never by the screen
    ], string="حالة المطبخ", default="none", copy=False, index=True)
    resta_kds_ready_date = fields.Datetime("جاهز منذ", copy=False)

    # ------------------------------------------------------------------ triggers
    @api.model_create_multi
    def create(self, vals_list):
        orders = super().create(vals_list)
        orders._resta_kds_dispatch(paid_now=True)
        return orders

    def write(self, vals):
        before = {o.id: o.last_order_preparation_change for o in self} \
            if "last_order_preparation_change" in vals else {}
        was_paid = {o.id for o in self if o.state in PAID_STATES}
        res = super().write(vals)
        if self.env.context.get("resta_kds_no_dispatch"):
            return res
        changed = self.filtered(lambda o: o.id in before and before[o.id] != o.last_order_preparation_change)
        if vals.get("state") in PAID_STATES + ("cancel",) or "lines" in vals \
                or self.env.context.get("resta_kds_fire"):
            changed |= self
        if changed:
            # paid in this write: send what the kitchen has not received yet
            changed.filtered(lambda o: o.id not in was_paid)._resta_kds_dispatch(paid_now=True)
            changed.filtered(lambda o: o.id in was_paid)._resta_kds_dispatch()
        return res

    def resta_kds_fire(self):
        """Send what is on the order now to the kitchen screens (used by «screen and printers
        in parallel»). Safe to call any number of times."""
        self.check_access("write")
        self.filtered(lambda o: o.state == "draft").with_context(resta_kds_fire=True)._resta_kds_dispatch()
        return True

    def resta_kds_resend(self, label=None):
        """The cashier sends the whole order again (lost ticket, dish to redo). The copy is marked
        «إعادة إرسال» and never changes what the kitchen is considered to have received."""
        self.check_access("read")
        Display = self.env["resta.kds.display"].sudo()
        for order in self.sudo():
            items = [(l, l.qty) for l in order.lines
                     if l.uuid and l.qty > 0 and not getattr(l, "skip_change", False)]
            for display in Display.search([("pos_config_ids", "in", order.config_id.id)]):
                relevant = [(l, q) for l, q in items if l._resta_kds_relevant(display)]
                if not relevant:
                    continue
                ticket = order._resta_kds_new_ticket(display, relevant)
                ticket.write({"is_resend": True, "info": " · ".join(
                    x for x in (label or _("إعادة إرسال"), ticket.info) if x)})
                display._notify("new", ticket.ids)
            order._resta_kds_refresh_status()
        return True

    # ------------------------------------------------------------------ dispatcher
    def _resta_kds_targets(self, paid_now=False):
        """{line uuid: quantity the kitchen must have} for this order, or None = leave as is."""
        self.ensure_one()
        if self.state == "cancel":
            return {}
        lines = self.lines
        if self.env.context.get("resta_kds_fire"):
            # sent explicitly (screen first, delivery apps...): what is on the order now
            return {l.uuid: l.qty for l in lines if l.uuid and not getattr(l, "skip_change", False)}
        config = self.config_id
        full = False
        if self.state in PAID_STATES:
            if not (paid_now and config.resta_kds_send_on_payment):
                return None  # after payment nothing is added or cancelled any more
            full = True  # paid now: everything not sent yet goes to the kitchen
        source = self._fields.get("source") and self.source
        if source in ("mobile", "kiosk"):
            pay_after = getattr(config, "self_ordering_pay_after", "meal")
            if pay_after == "meal" or self.state in PAID_STATES:
                full = True  # a customer's own order is "sent" when placed
        if full:
            return {l.uuid: l.qty for l in lines if l.uuid}
        targets = defaultdict(float)
        raw = self.last_order_preparation_change
        if raw:
            try:
                change = json.loads(raw)
            except ValueError:
                change = {}
            for item in (change.get("lines") or {}).values():
                if isinstance(item, dict) and item.get("uuid"):
                    targets[item["uuid"]] += float(item.get("quantity") or 0)
        live = {l.uuid for l in lines}
        return {u: q for u, q in targets.items() if u in live}

    def _resta_kds_dispatch(self, paid_now=False):
        Display = self.env["resta.kds.display"].sudo()
        for order in self.sudo():
            if not order.config_id:
                continue
            displays = Display.search([("pos_config_ids", "in", order.config_id.id)])
            if not displays:
                continue
            try:
                with self.env.cr.savepoint():
                    order._resta_kds_dispatch_one(displays, paid_now)
            except Exception:  # noqa: BLE001 - never block a sale because of the kitchen screen
                if self.env.context.get("resta_kds_raise"):
                    raise  # the caller (e.g. a delivery integration) handles and retries
                _logger.exception("resta_kds: dispatch failed for order %s", order.id)

    def _resta_kds_dispatch_one(self, displays, paid_now=False):
        self.ensure_one()
        targets = self._resta_kds_targets(paid_now)
        if targets is None:
            return
        only_add = self.state in PAID_STATES
        lines_by_uuid = {l.uuid: l for l in self.lines if l.uuid}
        Line = self.env["resta.kds.ticket.line"].sudo()
        touched = defaultdict(list)
        for display in displays:
            existing = Line.search([("pos_order_id", "=", self.id), ("display_id", "=", display.id),
                                    ("ticket_id.is_resend", "=", False)])
            sent = defaultdict(float)
            for l in existing:
                sent[l.pos_line_uuid] += l.qty - l.qty_cancelled
            new_items = []
            for uuid in set(targets) | set(sent):
                line = lines_by_uuid.get(uuid)
                product = line.product_id if line else existing.filtered(
                    lambda x: x.pos_line_uuid == uuid)[:1].product_id
                if line:
                    relevant = line._resta_kds_relevant(display)
                else:
                    relevant = display._accepts_product(product) if product else False
                if not relevant:
                    continue
                delta = round(targets.get(uuid, 0.0) - sent.get(uuid, 0.0), 4)
                if delta > 0 and line:
                    new_items.append((line, delta))
                elif delta < 0 and not only_add:
                    self._resta_kds_cancel(existing.filtered(lambda x: x.pos_line_uuid == uuid), -delta, touched)
            if new_items:
                ticket = self._resta_kds_new_ticket(display, new_items)
                touched[display].append(ticket.id)
            # notes / modifiers changed on lines still being prepared
            for l in existing.filtered(lambda x: not x.ticket_id.done):
                line = lines_by_uuid.get(l.pos_line_uuid)
                if not line:
                    continue
                if note_text(line.note) != (l.note or ""):
                    l.note = note_text(line.note)
                    touched[display].append(l.ticket_id.id)
                current = line._resta_kds_modifiers()
                if current != (l.modifiers_ack or []):
                    l.write({"modifiers": _modifier_delta(l.modifiers_ack or [], current),
                             "modifiers_ack": current, "changed": True, "done": False})
                    touched[display].append(l.ticket_id.id)
        for display, ids in touched.items():
            display._notify("new" if ids else "update", ids)
        if touched:
            self._resta_kds_refresh_status()

    def _resta_kds_cancel(self, ticket_lines, qty, touched):
        for l in ticket_lines.sorted(lambda x: x.id, reverse=True):
            if qty <= 0:
                break
            available = l.qty - l.qty_cancelled
            take = min(available, qty)
            if take > 0:
                l.qty_cancelled += take
                qty -= take
                touched[l.display_id].append(l.ticket_id.id)

    def _resta_kds_header(self):
        self.ensure_one()
        table = ""
        if "table_id" in self._fields and self.table_id:
            table = str(self.table_id.table_number)
        order_type = self.preset_id.name if self.preset_id else ""
        source = self._fields.get("source") and self.source or "pos"
        customer = self.partner_id.name if self.partner_id else (self.floating_order_name or "")
        note = "\n".join(x for x in (note_text(self.general_customer_note), note_text(self.internal_note)) if x)
        return {"tracking_number": self.tracking_number or "", "table_label": table, "order_type": order_type,
                "customer": customer, "note": note, "source": source}

    def _resta_kds_new_ticket(self, display, items):
        Ticket = self.env["resta.kds.ticket"].sudo()
        send_index = Ticket.search_count([("pos_order_id", "=", self.id), ("display_id", "=", display.id)]) + 1
        main_lang = display.main_lang_id.code or self.env.lang
        second_lang = display.secondary_lang_id.code
        line_vals = []
        for seq, (line, qty) in enumerate(items):
            product = line.product_id.with_context(display_default_code=False)
            name = product.with_context(lang=main_lang).display_name
            name2 = product.with_context(lang=second_lang).display_name if second_lang else ""
            attrs = line.with_context(lang=main_lang)._resta_kds_attributes()
            modifiers = line.with_context(lang=main_lang)._resta_kds_modifiers()
            line_vals.append((0, 0, {
                "sequence": seq, "pos_line_uuid": line.uuid, "pos_line_id": line.id,
                "combo_parent_uuid": line._resta_kds_parent_uuid(),
                "product_id": product.id, "name": name, "name_secondary": name2 if name2 != name else "",
                "attributes": attrs, "note": note_text(line.note), "qty": qty,
                "modifiers": modifiers, "modifiers_ack": modifiers,
            }))
        vals = dict(self._resta_kds_header(), display_id=display.id, pos_order_id=self.id,
                    stage_id=display._first_stage().id, send_index=send_index, line_ids=line_vals)
        return Ticket.create(vals)

    # ------------------------------------------------------------------ status / hooks
    def _resta_kds_refresh_status(self):
        for order in self.sudo():
            tickets = order.resta_kds_ticket_ids.filtered(lambda t: not t.cancelled)
            if not tickets:
                status = "none"
            elif all(t.done or t.stage_id.is_ready for t in tickets):
                status = "ready"
            else:
                status = "preparing"
            if status != order.resta_kds_status:
                was_ready = order.resta_kds_status in ("ready", "served")
                order.with_context(resta_kds_no_dispatch=True).write({
                    "resta_kds_status": status,
                    "resta_kds_ready_date": fields.Datetime.now() if status == "ready"
                    and not was_ready else order.resta_kds_ready_date,
                })
                if status == "ready" and not was_ready:
                    order._resta_kds_on_ready()
                order._resta_kds_on_status(status)
            order._resta_kds_on_change()

    def _resta_kds_on_ready(self):
        """Hook: the whole order is ready (customer pager, delivery apps, notifications)."""
        return True

    def _resta_kds_on_status(self, status):
        """Hook: any kitchen status change (none / preparing / ready / served)."""
        return True

    def _resta_kds_on_change(self):
        """Hook: any move on this order's tickets (new ticket, stage, bump, recall, cancel),
        e.g. a customer pager that follows a specific stage of a specific screen."""
        return True


class PosOrderLine(models.Model):
    _inherit = "pos.order.line"

    def _resta_kds_route_products(self):
        """Products deciding which kitchen screen gets this line (hook: a modifier follows its dish)."""
        self.ensure_one()
        if self.combo_line_ids:
            return self.combo_line_ids.product_id  # a combo header goes with its items
        return self.product_id

    def _resta_kds_relevant(self, display):
        self.ensure_one()
        return any(display._accepts_product(p) for p in self._resta_kds_route_products())

    def _resta_kds_attributes(self):
        """Options printed under the dish (attribute values by default)."""
        self.ensure_one()
        return ", ".join(self.attribute_value_ids.mapped("name"))

    def _resta_kds_modifiers(self):
        """Hook: [{"key", "name", "kind": "add"/"remove"/"instruction", "qty"}] shown under the dish."""
        return []

    def _resta_kds_parent_uuid(self):
        """Line shown under another one on the screen (combo item under its combo, modifier under its dish)."""
        self.ensure_one()
        return self.combo_parent_id.uuid if self.combo_parent_id else False

