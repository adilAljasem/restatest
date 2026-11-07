# -*- coding: utf-8 -*-
from odoo import api, fields, models

DEFAULT_CARD_SELECTOR = (
    ".o_pdis_order_card, .o_pdis_order, "
    "[class*='pdis'][class*='order_card'], "
    "[class*='prep'][class*='order_card'], "
    "[class*='kitchen'][class*='order_card']"
)
DEFAULT_ROOT_SELECTOR = (
    ".o_pdis_app, .o_pdis_display, [class*='o_pdis'], "
    "[class*='preparation_display'], [class*='prep_display']"
)


class PosKdsSoundSettings(models.Model):
    _name = "pos.kds.sound.settings"
    _description = "Kitchen Display Sound Settings"

    name = fields.Char(default="إعدادات صوت شاشة المطبخ", required=True)
    enabled = fields.Boolean(string="تفعيل الصوت", default=True)
    tone = fields.Selection(
        [
            ("chime", "جرس (ثلاث نغمات)"),
            ("bell", "رنين مزدوج"),
            ("beep", "صفارة"),
            ("custom", "ملف صوتي مخصص"),
        ],
        string="نوع الصوت",
        default="chime",
        required=True,
    )
    custom_sound = fields.Binary(string="ملف الصوت (mp3 / wav / ogg)", attachment=True)
    custom_sound_name = fields.Char(string="اسم الملف")
    volume = fields.Integer(string="مستوى الصوت (0-100)", default=80)
    repeat = fields.Integer(string="عدد مرات التشغيل لكل طلب", default=2)
    repeat_interval = fields.Integer(
        string="تكرار التنبيه كل (ثانية) حتى يلمس أحد الشاشة",
        default=0,
        help="0 = بدون تكرار. مثال: 15 يعني يعاد التنبيه كل 15 ثانية حتى يضغط أحد على الشاشة.",
    )
    card_selector = fields.Char(
        string="محدد بطاقة الطلب (CSS)",
        default=DEFAULT_CARD_SELECTOR,
        help="عنصر CSS يمثل بطاقة الطلب الواحدة على شاشة المطبخ. يزيد عدد هذه العناصر = طلب جديد.",
    )
    root_selector = fields.Char(
        string="محدد شاشة المطبخ (CSS)",
        default=DEFAULT_ROOT_SELECTOR,
        help="عنصر يدل على أن الصفحة المفتوحة هي شاشة المطبخ.",
    )
    extra_bundles = fields.Char(
        string="حزم أصول إضافية (Asset bundles)",
        help="أسماء حزم إضافية مفصولة بفاصلة، إن لم تُكتشف حزمة شاشة المطبخ تلقائيًا.",
    )
    bundles_info = fields.Text(string="الحزم المرتبطة حاليًا", readonly=True)

    @api.model
    def get_settings(self):
        rec = self.sudo().search([], limit=1)
        if not rec:
            rec = self.sudo().create({})
        return rec

    @api.model
    def action_open_settings(self):
        rec = self.get_settings()
        return {
            "type": "ir.actions.act_window",
            "name": "صوت شاشة المطبخ",
            "res_model": self._name,
            "res_id": rec.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_rescan_bundles(self):
        from ..hooks import sync_assets

        self.ensure_one()
        extra = (self.extra_bundles or "").split(",")
        bundles = sync_assets(self.env, extra)
        self.bundles_info = "\n".join(bundles)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "تم",
                "message": "تم ربط الصوت بالحزم: %s" % ", ".join(bundles),
                "type": "success",
                "sticky": False,
            },
        }

    def action_reset_selectors(self):
        self.write({
            "card_selector": DEFAULT_CARD_SELECTOR,
            "root_selector": DEFAULT_ROOT_SELECTOR,
        })

    def to_client_config(self):
        self.ensure_one()
        sound_url = False
        if self.tone == "custom" and self.custom_sound:
            stamp = int(self.write_date.timestamp()) if self.write_date else 0
            sound_url = "/pos_kds_sound/sound?v=%s" % stamp
        return {
            "enabled": bool(self.enabled),
            "tone": self.tone if (self.tone != "custom" or sound_url) else "chime",
            "sound_url": sound_url,
            "volume": max(0, min(100, self.volume or 0)),
            "repeat": max(1, min(10, self.repeat or 1)),
            "repeat_interval": max(0, self.repeat_interval or 0),
            "card_selector": self.card_selector or DEFAULT_CARD_SELECTOR,
            "root_selector": self.root_selector or DEFAULT_ROOT_SELECTOR,
        }
