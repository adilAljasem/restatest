import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


def note_text(raw):
    """POS notes may be plain text or a JSON list of {text, colorIndex} tags."""
    if not raw:
        return ""
    if isinstance(raw, list):
        return ", ".join((n.get("text") if isinstance(n, dict) else str(n)) for n in raw if n)
    raw = str(raw).strip()
    if raw.startswith("["):
        try:
            return note_text(json.loads(raw))
        except ValueError:
            return raw
    return raw


class RestaKdsTicket(models.Model):
    """One "send to kitchen" of one order, for one kitchen screen."""
    _name = "resta.kds.ticket"
    _description = "تذكرة المطبخ"
    _order = "create_date desc, id desc"

    display_id = fields.Many2one("resta.kds.display", required=True, ondelete="cascade", index=True)
    pos_order_id = fields.Many2one("pos.order", required=True, ondelete="cascade", index=True)
    config_id = fields.Many2one(related="pos_order_id.config_id", store=True)
    stage_id = fields.Many2one("resta.kds.stage", "المرحلة", ondelete="restrict")
    stage_date = fields.Datetime("دخل المرحلة", default=fields.Datetime.now)
    done = fields.Boolean("منتهية", index=True)
    done_date = fields.Datetime("وقت الانتهاء")
    ready_date = fields.Datetime("وقت الجاهزية")
    send_index = fields.Integer("رقم الإرسال", default=1, help="1 = الطلب الأول، 2+ = إضافة على نفس الطلب")
    tracking_number = fields.Char("رقم الطلب")
    info = fields.Char("معلومات إضافية", help="مثل: تطبيق التوصيل ورقم طلبه، أو الكاشير.")
    is_resend = fields.Boolean("إعادة إرسال", help="نسخة أعادها الكاشير؛ لا تُحسب ضمن ما أُرسل للمطبخ.")
    table_label = fields.Char("الطاولة")
    order_type = fields.Char("نوع الطلب")
    customer = fields.Char("الزبون")
    note = fields.Text("ملاحظة الطلب")
    source = fields.Char("المصدر")
    line_ids = fields.One2many("resta.kds.ticket.line", "ticket_id", "الأصناف")
    cancelled = fields.Boolean("ملغاة", compute="_compute_cancelled", store=True)

    @api.depends("line_ids.qty", "line_ids.qty_cancelled")
    def _compute_cancelled(self):
        for t in self:
            t.cancelled = bool(t.line_ids) and all(l.qty_cancelled >= l.qty for l in t.line_ids)

    # ------------------------------------------------------------------ UI
    def _ui(self, short=False):
        self.ensure_one()
        data = {
            "id": self.id, "order_id": self.pos_order_id.id, "number": self.tracking_number or "",
            "order_name": self.pos_order_id.pos_reference or self.pos_order_id.name,
            "table": self.table_label or "", "type": self.order_type or "", "customer": self.customer or "",
            "note": self.note or "", "source": self.source or "", "send_index": self.send_index,
            "info": self.info or "",
            "stage_id": self.stage_id.id, "done": self.done, "cancelled": self.cancelled,
            "created": fields.Datetime.to_string(self.create_date),
            "stage_date": fields.Datetime.to_string(self.stage_date),
            "done_date": fields.Datetime.to_string(self.done_date) if self.done_date else False,
        }
        if not short:
            lines = self.line_ids.sorted(lambda l: (l.sequence, l.id))
            data["lines"] = [l._ui() for l in lines]
        return data

    # ------------------------------------------------------------------ actions (kitchen screen)
    def _check_kitchen(self):
        if not self.env.user.has_group("resta_kds.group_kds_user"):
            raise UserError(_("هذه الشاشة لمستخدمي المطبخ."))

    def kds_next(self):
        """Tap on the ticket: next stage, or done after the last one."""
        self._check_kitchen()
        for t in self.sudo():
            if t.done:
                continue
            nxt = t.display_id._next_stage(t.stage_id)
            if nxt:
                t._set_stage(nxt)
            else:
                t._finish()
        self.sudo().display_id._notify("update", self.ids)
        return True

    def kds_set_stage(self, stage_id):
        self._check_kitchen()
        stage = self.env["resta.kds.stage"].sudo().browse(stage_id)
        for t in self.sudo():
            if stage.display_id == t.display_id:
                t.write({"done": False, "done_date": False})
                t._set_stage(stage)
        self.sudo().display_id._notify("update", self.ids)
        return True

    def kds_done(self):
        self._check_kitchen()
        for t in self.sudo().filtered(lambda x: not x.done):
            t._finish()
        self.sudo().display_id._notify("update", self.ids)
        return True

    def kds_recall(self):
        """Bring a finished ticket back to the screen (on its last stage)."""
        self._check_kitchen()
        for t in self.sudo():
            last = t.display_id.stage_ids.sorted("sequence")[-1:]
            t.write({"done": False, "done_date": False, "stage_id": last.id, "stage_date": fields.Datetime.now()})
            t.pos_order_id._resta_kds_refresh_status()
        self.sudo().display_id._notify("update", self.ids)
        return True

    def _set_stage(self, stage):
        self.ensure_one()
        vals = {"stage_id": stage.id, "stage_date": fields.Datetime.now()}
        if stage.is_ready and not self.ready_date:
            vals["ready_date"] = fields.Datetime.now()
        self.write(vals)
        self.pos_order_id._resta_kds_refresh_status()

    def _finish(self):
        self.ensure_one()
        now = fields.Datetime.now()
        vals = {"done": True, "done_date": now}
        if not self.ready_date:
            vals["ready_date"] = now
        self.write(vals)
        self.line_ids.filtered(lambda l: not l.done).write({"done": True})
        self.pos_order_id._resta_kds_refresh_status()


class RestaKdsTicketLine(models.Model):
    _name = "resta.kds.ticket.line"
    _description = "صنف في تذكرة المطبخ"
    _order = "sequence, id"

    ticket_id = fields.Many2one("resta.kds.ticket", required=True, ondelete="cascade", index=True)
    display_id = fields.Many2one(related="ticket_id.display_id", store=True, index=True)
    pos_order_id = fields.Many2one(related="ticket_id.pos_order_id", store=True, index=True)
    sequence = fields.Integer(default=10)
    pos_line_uuid = fields.Char(index=True)
    pos_line_id = fields.Many2one("pos.order.line", ondelete="set null")
    combo_parent_uuid = fields.Char()
    product_id = fields.Many2one("product.product", "الصنف")
    name = fields.Char("الاسم")
    name_secondary = fields.Char("الاسم باللغة الثانية")
    attributes = fields.Char("الخيارات")
    note = fields.Char("ملاحظة")
    modifiers = fields.Json("الإضافات", help="[{key, name, kind: add/remove/instruction, qty, status}]")
    modifiers_ack = fields.Json(help="Last modifiers sent to the kitchen, to show what changed.")
    changed = fields.Boolean("عُدّل بعد الإرسال")
    qty = fields.Float("الكمية", digits=(16, 2))
    qty_cancelled = fields.Float("الملغي", digits=(16, 2))
    done = fields.Boolean("جاهز")

    def _ui(self):
        self.ensure_one()
        return {
            "id": self.id, "uuid": self.pos_line_uuid or "", "parent_uuid": self.combo_parent_uuid or "",
            "product_id": self.product_id.id, "name": self.name or "", "name2": self.name_secondary or "",
            "attributes": self.attributes or "", "note": self.note or "",
            "modifiers": self.modifiers or [], "changed": self.changed,
            "qty": self.qty, "cancelled": self.qty_cancelled, "done": self.done,
            "category_ids": self.product_id.pos_categ_ids.ids,
        }

    def kds_toggle(self):
        """Tap on a line: prepared / not prepared. Auto-advance when the whole ticket is prepared."""
        self.env["resta.kds.ticket"]._check_kitchen()
        lines = self.sudo()
        for line in lines:
            line.done = not line.done
            if line.done and line.changed:
                line.changed = False
        tickets = lines.ticket_id
        for t in tickets:
            if t.display_id.auto_advance and not t.done and all(
                    l.done or l.qty_cancelled >= l.qty for l in t.line_ids):
                nxt = t.display_id._next_stage(t.stage_id)
                if nxt:
                    t._set_stage(nxt)
                else:
                    t._finish()
        tickets.display_id._notify("update", tickets.ids)
        return True
