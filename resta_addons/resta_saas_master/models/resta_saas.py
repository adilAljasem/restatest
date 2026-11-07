import re
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import html2plaintext

from .resta_tenant import BUSINESS_TYPES, EDITIONS, STATES


class RestaPlan(models.Model):
    _name = "resta.plan"
    _description = "Resta subscription plan"
    _order = "sequence, id"

    name = fields.Char("الباقة", required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    tagline = fields.Char("وصف قصير", translate=True)
    price_monthly = fields.Monetary("السعر الشهري")
    price_yearly = fields.Monetary("السعر السنوي")
    currency_id = fields.Many2one("res.currency", default=lambda s: s.env.ref("base.SAR"))
    max_branches = fields.Integer("الفروع المشمولة", default=1)
    extra_branch_price = fields.Monetary("سعر الفرع الإضافي شهرياً")
    features = fields.Text("المزايا (سطر لكل ميزة)", translate=True)
    highlighted = fields.Boolean("مميزة في صفحة الأسعار")
    published = fields.Boolean("تظهر في الموقع", default=True)
    product_id = fields.Many2one("product.product", "منتج الفوترة", readonly=True)

    def feature_list(self):
        self.ensure_one()
        return [line.strip("-• ").strip() for line in (self.features or "").splitlines() if line.strip()]

    def _billing_product(self):
        self.ensure_one()
        if not self.product_id:
            self.product_id = self.env["product.product"].sudo().create({
                "name": _("اشتراك ريستا - %s", self.name), "type": "service",
                "sale_ok": True, "purchase_ok": False, "list_price": self.price_monthly})
        return self.product_id


class RestaSubscription(models.Model):
    _name = "resta.subscription"
    _description = "Resta subscription period"
    _order = "date_start desc"

    tenant_id = fields.Many2one("resta.tenant", required=True, ondelete="cascade", index=True)
    plan_id = fields.Many2one("resta.plan", required=True)
    billing = fields.Selection([("monthly", "شهري"), ("yearly", "سنوي")], default="yearly", required=True)
    branches = fields.Integer("الفروع", default=1)
    date_start = fields.Date("من", required=True, default=fields.Date.context_today)
    date_end = fields.Date("إلى", required=True)
    amount = fields.Monetary("المبلغ قبل الضريبة")
    currency_id = fields.Many2one(related="plan_id.currency_id")
    invoice_id = fields.Many2one("account.move", "الفاتورة", readonly=True)
    state = fields.Selection([("active", "سارية"), ("cancelled", "ملغاة")], default="active")


class RestaAnnouncement(models.Model):
    _name = "resta.announcement"
    _description = "Resta announcement to tenants (changelog, maintenance, messages)"
    _inherit = ["mail.thread"]
    _order = "publish_date desc, id desc"

    name = fields.Char("العنوان", required=True, translate=True, tracking=True)
    body = fields.Html("المحتوى", translate=True, sanitize=True)
    kind = fields.Selection([
        ("feature", "ميزة جديدة"), ("enhancement", "تحسين"), ("fix", "إصلاح"),
        ("maintenance", "صيانة مجدولة"), ("message", "رسالة"),
    ], "النوع", default="feature", required=True)
    tag = fields.Char("الوسم", help="مثال: نقاط البيع، المخزون، لوحة البيانات")
    important = fields.Boolean("هام (يظهر كنافذة عند الدخول)")
    audience = fields.Selection([
        ("all", "كل المطاعم"), ("edition", "حسب النسخة"), ("state", "حسب حالة الاشتراك"), ("tenants", "مطاعم محددة"),
    ], "الجمهور", default="all", required=True)
    edition = fields.Selection(EDITIONS, "النسخة")
    tenant_state = fields.Selection([s for s in STATES if s[0] in ("trial", "active", "expired")], "الحالة")
    tenant_ids = fields.Many2many("resta.tenant", string="المطاعم")
    state = fields.Selection([("draft", "مسودة"), ("published", "منشور"), ("archived", "مؤرشف")],
                             default="draft", tracking=True)
    publish_date = fields.Datetime("تاريخ النشر", readonly=True)
    feedback_ids = fields.One2many("resta.feedback", "announcement_id")
    feedback_count = fields.Integer(compute="_compute_feedback")
    rating_avg = fields.Float("متوسط التقييم", compute="_compute_feedback")

    def _compute_feedback(self):
        for a in self:
            rated = a.feedback_ids.filtered("rating")
            a.feedback_count = len(a.feedback_ids)
            a.rating_avg = sum(int(f.rating) for f in rated) / len(rated) if rated else 0.0

    def action_publish(self):
        self.write({"state": "published", "publish_date": fields.Datetime.now()})

    def action_archive_announcement(self):
        self.write({"state": "archived"})

    def action_draft(self):
        self.write({"state": "draft"})

    @api.model
    def _for_tenant(self, tenant, last_id=0):
        anns = self.search([("state", "=", "published"), ("id", ">", last_id)], order="id")
        return anns.filtered(lambda a: a.audience == "all"
                             or (a.audience == "edition" and a.edition == tenant.edition)
                             or (a.audience == "state" and a.tenant_state == tenant.state)
                             or (a.audience == "tenants" and tenant in a.tenant_ids))

    def _payload(self):
        self.ensure_one()
        lang = "ar_001"
        rec = self.with_context(lang=lang)
        return {"id": self.id, "title": rec.name, "body": rec.body or "", "kind": self.kind,
                "tag": self.tag or "", "important": self.important,
                "date": fields.Datetime.to_string(self.publish_date or self.create_date)}


class RestaFeedback(models.Model):
    _name = "resta.feedback"
    _description = "Feedback and feature suggestions from tenants"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc"

    tenant_id = fields.Many2one("resta.tenant", "المطعم", required=True, ondelete="cascade", index=True)
    account_manager_id = fields.Many2one(related="tenant_id.account_manager_id", store=True)
    kind = fields.Selection([("suggestion", "اقتراح ميزة"), ("rating", "تقييم تحديث"), ("support", "طلب دعم")],
                            "النوع", required=True, default="suggestion")
    announcement_id = fields.Many2one("resta.announcement", "التحديث")
    rating = fields.Selection([("1", "😩 غير راضٍ"), ("2", "😐 محايد"), ("3", "😀 راضٍ")], "التقييم")
    message = fields.Text("الرسالة")
    user_name = fields.Char("المستخدم")
    user_email = fields.Char("بريد المستخدم")
    state = fields.Selection([("new", "جديد"), ("review", "قيد الدراسة"), ("planned", "مخطط"), ("done", "منجز"),
                              ("rejected", "مرفوض")], default="new", tracking=True)

    @api.depends("tenant_id", "kind")
    def _compute_display_name(self):
        labels = dict(self._fields["kind"].selection)
        for f in self:
            f.display_name = "%s - %s" % (labels.get(f.kind), f.tenant_id.name or "")


class RestaDemoRequest(models.Model):
    _name = "resta.demo.request"
    _description = "Demo request from the Resta website"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc"

    name = fields.Char("الاسم", required=True)
    phone = fields.Char("الجوال", required=True)
    email = fields.Char("البريد")
    business_name = fields.Char("اسم النشاط")
    business_type = fields.Selection([(k, l) for k, l, _e in BUSINESS_TYPES], "نوع النشاط")
    branches = fields.Integer("عدد الفروع", default=1)
    city = fields.Char("المدينة")
    message = fields.Text("ملاحظات")
    account_manager_id = fields.Many2one("res.users", "مدير الحساب", tracking=True)
    state = fields.Selection([("new", "جديد"), ("contacted", "تم التواصل"), ("demo", "تم العرض"),
                              ("won", "اشترك"), ("lost", "لم يشترك")], default="new", tracking=True)
    tenant_id = fields.Many2one("resta.tenant", "حساب المطعم")

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        for r in recs:
            if not r.account_manager_id:
                r.account_manager_id = self.env["resta.tenant"]._next_account_manager()
            if r.account_manager_id:
                r.activity_schedule("mail.mail_activity_data_call", user_id=r.account_manager_id.id,
                                    summary=_("طلب عرض: تواصل مع %s", r.name))
        return recs

    def action_whatsapp(self):
        self.ensure_one()
        return {"type": "ir.actions.act_url", "target": "new",
                "url": "https://wa.me/%s" % re.sub(r"\D", "", self.phone or "")}


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    resta_tenant_url_pattern = fields.Char("رابط المطعم", config_parameter="resta.tenant_url_pattern",
                                           help="مثال: https://{db}.resta.sa")
    resta_master_url = fields.Char("رابط المنصة (للمزامنة)", config_parameter="resta.master_url")
    resta_db_prefix = fields.Char("بادئة قواعد البيانات", config_parameter="resta.db_prefix")
    resta_trial_days = fields.Integer("أيام التجربة", config_parameter="resta.trial_days")
    resta_template_dinein = fields.Char("قالب الداين إن", config_parameter="resta.template_dinein")
    resta_template_qsr = fields.Char("قالب الوجبات السريعة", config_parameter="resta.template_qsr")
    resta_support_phone = fields.Char("هاتف الدعم", config_parameter="resta.support_phone")
    resta_support_whatsapp = fields.Char("واتساب الدعم", config_parameter="resta.support_whatsapp")
    resta_support_email = fields.Char("بريد الدعم", config_parameter="resta.support_email")
    resta_signup_per_ip = fields.Integer("حد التسجيل لكل IP في الساعة", config_parameter="resta.signup_per_ip")
