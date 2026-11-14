import secrets

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


def _token(*_args):
    return secrets.token_urlsafe(24)


class RestaKdsDisplay(models.Model):
    """A kitchen screen (station). It receives the tickets of its points of sale,
    limited to its POS categories (empty = everything)."""
    _name = "resta.kds.display"
    _description = "شاشة المطبخ"
    _inherit = ["pos.load.mixin"]
    _order = "sequence, id"

    name = fields.Char("اسم الشاشة", required=True, default="المطبخ")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda s: s.env.company)
    pos_config_ids = fields.Many2many("pos.config", "resta_kds_display_pos_config_rel", "display_id", "config_id",
                                      string="نقاط البيع")
    category_ids = fields.Many2many("pos.category", "resta_kds_display_pos_category_rel", "display_id", "category_id",
                                    string="التصنيفات", help="فارغ = كل الأصناف (شاشة واحدة لكل المطبخ).")
    stage_ids = fields.One2many("resta.kds.stage", "display_id", "المراحل", copy=True)
    warn_minutes = fields.Integer("تنبيه أصفر بعد (دقيقة)", default=8)
    late_minutes = fields.Integer("تنبيه أحمر بعد (دقيقة)", default=15)
    auto_advance = fields.Boolean("أنهِ التذكرة تلقائياً عند تجهيز كل أصنافها", default=True)
    sound = fields.Boolean("صوت عند وصول طلب جديد", default=True)
    secondary_lang_id = fields.Many2one("res.lang", "اللغة الثانية لأسماء الأصناف",
                                        domain=[("active", "=", True)],
                                        help="مثلاً الإنجليزية: يظهر اسم الصنف بالعربي وتحته بالإنجليزي.")
    main_lang_id = fields.Many2one("res.lang", "لغة الأسماء الرئيسية", domain=[("active", "=", True)],
                                   default=lambda s: s.env["res.lang"].search([("code", "=like", "ar%")], limit=1))
    access_token = fields.Char(required=True, copy=False, default=_token, readonly=True, groups="point_of_sale.group_pos_user")
    tracking_title = fields.Char("عنوان شاشة الطلبات للزبائن", default="طلبك")
    tracking_ready_minutes = fields.Integer("مدة بقاء الرقم في «جاهز» (دقيقة)", default=10)
    open_count = fields.Integer("تذاكر مفتوحة", compute="_compute_open_count")

    _token_uniq = models.Constraint("UNIQUE(access_token)", "رمز الشاشة مكرر.")

    @api.model_create_multi
    def create(self, vals_list):
        displays = super().create(vals_list)
        for display in displays.filtered(lambda d: not d.stage_ids):
            display._create_default_stages()
        return displays

    def _create_default_stages(self):
        self.ensure_one()
        # Simple by default (like Foodics / Toast): one column, one «تم» tap = ready.
        # Bigger kitchens can add stages (e.g. «قيد التحضير», or an expo «جاهز» column).
        self.env["resta.kds.stage"].create([
            {"display_id": self.id, "name": _("للتحضير"), "sequence": 10, "color": "#FF5A1F"},
        ])

    @api.constrains("stage_ids")
    def _check_stages(self):
        for display in self:
            if not display.stage_ids:
                raise ValidationError(_("الشاشة تحتاج مرحلة واحدة على الأقل."))

    def _compute_open_count(self):
        groups = self.env["resta.kds.ticket"]._read_group(
            [("display_id", "in", self.ids), ("done", "=", False)], ["display_id"], ["__count"])
        counts = {d.id: c for d, c in groups}
        for display in self:
            display.open_count = counts.get(display.id, 0)

    # ------------------------------------------------------------------ helpers
    def _first_stage(self):
        return self.stage_ids.sorted("sequence")[:1]

    def _next_stage(self, stage):
        later = self.stage_ids.filtered(lambda s: s.sequence > stage.sequence or
                                        (s.sequence == stage.sequence and s.id > stage.id))
        return later.sorted(lambda s: (s.sequence, s.id))[:1]

    def _accepts_product(self, product):
        self.ensure_one()
        if not self.category_ids:
            return True
        return bool(product.pos_categ_ids & self.category_ids)

    def _channel(self):
        return self.sudo().access_token

    def _notify(self, kind, ticket_ids=()):
        for display in self:
            self.env["bus.bus"]._sendone(display._channel(), "RESTA_KDS", {
                "kind": kind, "display_id": display.id, "ticket_ids": list(ticket_ids),
            })

    def action_regenerate_token(self):
        for display in self:
            display.sudo().access_token = _token()
        return True

    def action_open_screen(self):
        self.ensure_one()
        return {
            "type": "ir.actions.client",
            "tag": "resta_kds.screen",
            "name": self.name,
            "params": {"display_id": self.id},
            "target": "fullscreen",
        }

    def action_open_tracking(self):
        self.ensure_one()
        return {"type": "ir.actions.act_url", "target": "new",
                "url": "/resta/order-status/%s" % self.sudo().access_token}

    def tracking_url(self):
        self.ensure_one()
        base = self.get_base_url()
        return "%s/resta/order-status/%s" % (base, self.sudo().access_token)

    # ------------------------------------------------------------------ POS loading
    @api.model
    def _load_pos_data_domain(self, data, config):
        return [("pos_config_ids", "in", config.id)]

    @api.model
    def _load_pos_data_fields(self, config):
        return ["id", "name", "category_ids"]

    # ------------------------------------------------------------------ screen data
    @api.model
    def kds_list_displays(self):
        return [{"id": d.id, "name": d.name, "open": d.open_count} for d in self.search([])]

    def kds_load(self):
        """Everything the kitchen screen needs, in one call."""
        self.ensure_one()
        self.check_access("read")
        Ticket = self.env["resta.kds.ticket"].sudo()
        tickets = Ticket.search([("display_id", "=", self.id), ("done", "=", False)], order="create_date, id")
        recent = Ticket.search([("display_id", "=", self.id), ("done", "=", True)], order="done_date desc", limit=12)
        cats = self.category_ids or self.env["pos.category"].sudo().search([])
        return {
            "display": {
                "id": self.id, "name": self.name, "warn": self.warn_minutes, "late": self.late_minutes,
                "sound": self.sound, "auto_advance": self.auto_advance,
                "channel": self._channel(),
                "stages": [{"id": s.id, "name": s.name, "color": s.color, "is_ready": s.is_ready}
                           for s in self.stage_ids.sorted("sequence")],
                "categories": [{"id": c.id, "name": c.name} for c in cats],
                "bilingual": bool(self.secondary_lang_id),
                "tracking_url": self.tracking_url(),
            },
            "tickets": [t._ui() for t in tickets],
            "recent": [t._ui(short=True) for t in recent],
            "server_time": fields.Datetime.to_string(fields.Datetime.now()),
        }


class RestaKdsStage(models.Model):
    _name = "resta.kds.stage"
    _description = "مرحلة في شاشة المطبخ"
    _order = "sequence, id"

    display_id = fields.Many2one("resta.kds.display", required=True, ondelete="cascade", index=True)
    name = fields.Char("المرحلة", required=True, translate=True)
    sequence = fields.Integer(default=10)
    color = fields.Char("اللون", default="#F59E0B")
    is_ready = fields.Boolean("تعني «جاهز» للزبون",
                              help="عند وصول كل تذاكر الطلب لهذه المرحلة يظهر رقمه في شاشة «طلبك جاهز».")
