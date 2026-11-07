import hashlib
import hmac
import logging
import os
import re
import secrets
import shutil
import time
from datetime import timedelta

import odoo
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import SQL

_logger = logging.getLogger(__name__)

STATES = [
    ("provisioning", "جارٍ التجهيز"),
    ("trial", "تجربة"),
    ("active", "مشترك"),
    ("expired", "منتهي"),
    ("suspended", "موقوف"),
    ("cancelled", "ملغى"),
    ("failed", "فشل التجهيز"),
]
EDITIONS = [("dinein", "مطعم (داين إن)"), ("qsr", "وجبات سريعة ومقاهي")]
BUSINESS_TYPES = [
    ("dinein", "مطعم بطاولات (داين إن)", "dinein"),
    ("qsr", "وجبات سريعة", "qsr"),
    ("cafe", "مقهى", "qsr"),
    ("bakery", "مخبز وحلويات", "qsr"),
    ("cloud", "مطبخ سحابي", "qsr"),
    ("foodtruck", "عربة طعام", "qsr"),
]
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class RestaTenant(models.Model):
    _name = "resta.tenant"
    _description = "Resta restaurant (tenant)"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc"
    _rec_name = "display_name"

    # --- Identity ---------------------------------------------------------
    account_number = fields.Char("رقم الحساب", readonly=True, copy=False, index=True, tracking=True)
    name = fields.Char("اسم النشاط", required=True, tracking=True)
    business_type = fields.Selection([(k, l) for k, l, _e in BUSINESS_TYPES], "نوع النشاط", required=True)
    edition = fields.Selection(EDITIONS, "النسخة", required=True, tracking=True)
    country_id = fields.Many2one("res.country", "الدولة", default=lambda s: s.env.ref("base.sa"))
    partner_id = fields.Many2one("res.partner", "العميل", tracking=True)
    owner_name = fields.Char("اسم المالك", required=True)
    owner_email = fields.Char("البريد", required=True, tracking=True)
    owner_phone = fields.Char("الجوال", tracking=True)
    branches_planned = fields.Integer("عدد الفروع المتوقع", default=1)

    # --- Lifecycle --------------------------------------------------------
    state = fields.Selection(STATES, default="provisioning", required=True, tracking=True, index=True)
    trial_end = fields.Date("نهاية التجربة", tracking=True)
    plan_id = fields.Many2one("resta.plan", "الباقة", tracking=True)
    subscription_ids = fields.One2many("resta.subscription", "tenant_id", "فترات الاشتراك")
    subscription_end = fields.Date("نهاية الاشتراك", compute="_compute_subscription", store=True, tracking=True)
    days_left = fields.Integer("الأيام المتبقية", compute="_compute_days_left")
    account_manager_id = fields.Many2one(
        "res.users", "مدير الحساب", tracking=True,
        domain=lambda s: [("group_ids", "in", s.env.ref("resta_saas_master.group_resta_account_manager").ids)])
    source = fields.Selection([("signup", "تسجيل ذاتي"), ("demo", "طلب عرض"), ("manual", "يدوي")],
                              default="signup", string="المصدر")
    notes = fields.Html("ملاحظات")

    # --- Technical --------------------------------------------------------
    db_name = fields.Char("قاعدة البيانات", readonly=True, copy=False, index=True)
    url = fields.Char("رابط النظام", compute="_compute_url")
    token = fields.Char("مفتاح الربط", readonly=True, copy=False, groups="resta_saas_master.group_resta_manager")
    access_token = fields.Char(readonly=True, copy=False, index=True)  # signup status page
    provision_step = fields.Char("مرحلة التجهيز", readonly=True)
    provision_log = fields.Text("سجل التجهيز", readonly=True)
    signup_ip = fields.Char(readonly=True)

    # --- Usage reported by the tenant (sync) ----------------------------
    last_sync = fields.Datetime("آخر مزامنة", readonly=True)
    usage_users = fields.Integer("المستخدمون", readonly=True)
    usage_pos = fields.Integer("نقاط البيع", readonly=True)
    usage_orders_30d = fields.Integer("طلبات آخر 30 يوم", readonly=True)
    usage_sales_30d = fields.Float("مبيعات آخر 30 يوم", readonly=True)
    usage_last_order = fields.Datetime("آخر طلب", readonly=True)
    usage_last_login = fields.Datetime("آخر دخول", readonly=True)
    usage_modules = fields.Integer("الموديولات المثبتة", readonly=True)

    feedback_ids = fields.One2many("resta.feedback", "tenant_id", "الملاحظات")
    feedback_count = fields.Integer(compute="_compute_counts")
    invoice_count = fields.Integer(compute="_compute_counts")
    color = fields.Integer(compute="_compute_color")

    _account_number_uniq = models.Constraint("UNIQUE(account_number)", "رقم الحساب مستخدم.")
    _db_name_uniq = models.Constraint("UNIQUE(db_name)", "قاعدة البيانات مستخدمة.")

    # ------------------------------------------------------------------ compute
    @api.depends("account_number", "name")
    def _compute_display_name(self):
        for t in self:
            t.display_name = "%s - %s" % (t.account_number, t.name) if t.account_number else t.name

    @api.depends("subscription_ids.date_end", "subscription_ids.state")
    def _compute_subscription(self):
        for t in self:
            ends = t.subscription_ids.filtered(lambda s: s.state != "cancelled").mapped("date_end")
            t.subscription_end = max(ends) if ends else False

    def _compute_days_left(self):
        today = fields.Date.context_today(self)
        for t in self:
            end = t.subscription_end if t.state == "active" else t.trial_end
            t.days_left = (end - today).days if end else 0

    def _compute_url(self):
        pattern = self.env["ir.config_parameter"].sudo().get_param(
            "resta.tenant_url_pattern", "https://{db}.resta.sa")
        for t in self:
            t.url = pattern.format(db=t.db_name) if t.db_name else False

    def _compute_counts(self):
        for t in self:
            t.feedback_count = len(t.feedback_ids)
            t.invoice_count = len(t.subscription_ids.mapped("invoice_id"))

    def _compute_color(self):
        colors = {"provisioning": 4, "trial": 2, "active": 10, "expired": 1, "suspended": 9, "cancelled": 0, "failed": 1}
        for t in self:
            t.color = colors.get(t.state, 0)

    # ------------------------------------------------------------------ create
    @api.model
    def edition_for(self, business_type):
        return {k: e for k, _l, e in BUSINESS_TYPES}.get(business_type, "qsr")

    @api.model_create_multi
    def create(self, vals_list):
        ICP = self.env["ir.config_parameter"].sudo()
        prefix = ICP.get_param("resta.db_prefix", "r")
        trial_days = int(ICP.get_param("resta.trial_days", "14"))
        for vals in vals_list:
            if not vals.get("account_number"):
                vals["account_number"] = self.env["ir.sequence"].sudo().next_by_code("resta.tenant.account")
            vals.setdefault("db_name", "%s%s" % (prefix, vals["account_number"]))
            vals.setdefault("token", secrets.token_urlsafe(32))
            vals.setdefault("access_token", secrets.token_urlsafe(24))
            if vals.get("business_type") and not vals.get("edition"):
                vals["edition"] = self.edition_for(vals["business_type"])
            vals.setdefault("trial_end", fields.Date.context_today(self) + timedelta(days=trial_days))
        tenants = super().create(vals_list)
        for t in tenants:
            if t.owner_email and not EMAIL_RE.match(t.owner_email):
                raise ValidationError(_("البريد الإلكتروني غير صحيح."))
            if not t.partner_id:
                company = self.env["res.partner"].sudo().create({
                    "name": t.name, "is_company": True, "country_id": t.country_id.id,
                    "phone": t.owner_phone, "email": t.owner_email, "ref": t.account_number})
                contact = self.env["res.partner"].sudo().create({
                    "name": t.owner_name, "parent_id": company.id, "email": t.owner_email,
                    "phone": t.owner_phone, "type": "contact"})
                t.partner_id = company
                t.message_subscribe(partner_ids=contact.ids)
            if not t.account_manager_id:
                t.account_manager_id = t._next_account_manager()
        return tenants

    def _next_account_manager(self):
        """Round-robin over the Resta account managers (fewest open tenants first)."""
        group = self.env.ref("resta_saas_master.group_resta_account_manager")
        users = group.sudo().user_ids.filtered(lambda u: u.active and not u.share)
        if not users:
            return self.env["res.users"]
        load = dict(self.sudo()._read_group(
            [("account_manager_id", "in", users.ids), ("state", "in", ("trial", "active", "provisioning"))],
            ["account_manager_id"], ["__count"]))
        return min(users, key=lambda u: (load.get(u, 0), u.id))

    # ------------------------------------------------------------ provisioning
    def _log(self, message):
        self.ensure_one()
        stamp = fields.Datetime.now().strftime("%H:%M:%S")
        self.sudo().write({"provision_step": message,
                           "provision_log": "%s[%s] %s\n" % (self.provision_log or "", stamp, message)})
        self.env.cr.commit()

    def _template_db(self):
        return self.env["ir.config_parameter"].sudo().get_param(
            "resta.template_%s" % self.edition, "resta_tpl_%s" % self.edition)

    @api.model
    def _cron_provision(self, limit=3):
        for tenant in self.search([("state", "=", "provisioning")], limit=limit, order="id"):
            try:
                tenant._provision()
            except Exception as exc:  # noqa: BLE001 - recorded on the tenant
                self.env.cr.rollback()
                _logger.exception("Resta: provisioning %s failed", tenant.db_name)
                tenant._log("فشل: %s" % exc)
                tenant.sudo().write({"state": "failed"})
                tenant.message_post(body=_("فشل تجهيز قاعدة البيانات: %s", exc))
                self.env.cr.commit()

    def _provision(self):
        self.ensure_one()
        template, db = self._template_db(), self.db_name
        if not re.match(r"^[a-z0-9][a-z0-9_]{2,62}$", db or ""):
            raise UserError(_("اسم قاعدة بيانات غير صالح: %s", db))
        existing = self._pg_databases()
        if template not in existing:
            raise UserError(_("قالب النسخة غير موجود: %s", template))
        if db not in existing:
            self._log("إنشاء قاعدة البيانات من القالب %s" % template)
            self._clone_database(template, db)
        self._log("تجهيز بيانات المطعم والمستخدم")
        registry = odoo.modules.registry.Registry(db)
        # NB: not named "env" - odoo's _() looks up "env" in the caller's frame.
        with registry.cursor() as tenant_cr:
            self._setup_tenant_database(odoo.api.Environment(tenant_cr, odoo.SUPERUSER_ID, {}))
        self._log("جاهز")
        self.sudo().write({"state": "trial"})
        template_mail = self.env.ref("resta_saas_master.mail_template_welcome", raise_if_not_found=False)
        if template_mail:
            template_mail.sudo().send_mail(self.id, force_send=False)
        if self.account_manager_id:
            self.activity_schedule(
                "mail.mail_activity_data_call", user_id=self.account_manager_id.id,
                summary=_("اتصال ترحيبي بالعميل الجديد"),
                note=_("عميل جديد بدأ التجربة: %s (%s) - %s", self.name, self.owner_name, self.owner_phone or ""))
        self.env.cr.commit()

    @api.model
    def _pg_databases(self):
        """All databases, including the edition templates (flagged IS_TEMPLATE, hence
        hidden from Odoo's database list, the dbfilter and the cron workers)."""
        with odoo.sql_db.db_connect("postgres").cursor() as cr:
            cr.execute("SELECT datname FROM pg_database")
            return {r[0] for r in cr.fetchall()}

    @api.model
    def _clone_database(self, template, db):
        """CREATE DATABASE ... TEMPLATE, new uuid, filestore copy (no db-manager dependency)."""
        odoo.sql_db.close_db(template)
        conn = odoo.sql_db.db_connect("postgres")
        with conn.cursor() as cr:
            cr._cnx.autocommit = True
            cr.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                       "WHERE datname = %s AND pid != pg_backend_pid()", (template,))
            cr.execute(SQL("CREATE DATABASE %s ENCODING 'unicode' TEMPLATE %s",
                           SQL.identifier(db), SQL.identifier(template)))
        src, dst = odoo.tools.config.filestore(template), odoo.tools.config.filestore(db)
        if os.path.exists(src) and not os.path.exists(dst):
            shutil.copytree(src, dst)

    def _master_url(self):
        ICP = self.env["ir.config_parameter"].sudo()
        return ICP.get_param("resta.master_url") or ICP.get_param("web.base.url")

    def _setup_tenant_database(self, env):
        """Runs inside the new tenant database (env = superuser env of that db)."""
        env["ir.config_parameter"].init(force=True)  # new database.uuid / secret
        company = env.ref("base.main_company")
        company.write({"name": self.name, "email": self.owner_email, "phone": self.owner_phone,
                       "country_id": env["res.country"].search([("code", "=", self.country_id.code)], limit=1).id
                       or company.country_id.id})
        lang = "ar_001" if env["res.lang"]._lang_get("ar_001") else "en_US"
        admin = env.ref("base.user_admin")
        admin.write({"name": self.owner_name, "login": self.owner_email.strip().lower(),
                     "email": self.owner_email, "lang": lang, "tz": "Asia/Riyadh"})
        admin.partner_id.phone = self.owner_phone
        pw_hash = self.sudo().signup_password_hash
        if pw_hash:
            admin._set_encrypted_password(admin.id, pw_hash)
        else:
            admin._change_password(secrets.token_urlsafe(16))
        params = {
            "web.base.url": self.url,
            "web.base.url.freeze": "True",
            "resta.master_url": self._master_url(),
            "resta.account_number": self.account_number,
            "resta.tenant_token": self.sudo().token,
            "resta.edition": self.edition,
            "resta.subscription_state": "trial",
            "resta.trial_end": str(self.trial_end or ""),
            "resta.business_name": self.name,
        }
        for key, value in params.items():
            env["ir.config_parameter"].set_param(key, value or "")
        self.sudo().write({"signup_password_hash": False})

    # Hash (Odoo crypt context) of the password typed at signup. Never the clear
    # password; written as-is into the tenant's admin user, then wiped.
    signup_password_hash = fields.Char(groups="base.group_system", copy=False)

    @api.model
    def hash_password(self, password):
        return self.env["res.users"]._crypt_context().hash(password)

    # --------------------------------------------------------------- lifecycle
    @api.model
    def _cron_lifecycle(self):
        today = fields.Date.context_today(self)
        for t in self.search([("state", "=", "trial"), ("trial_end", "<", today)]):
            t.state = "expired"
            t.message_post(body=_("انتهت الفترة التجريبية."))
        for t in self.search([("state", "=", "active"), ("subscription_end", "<", today)]):
            t.state = "expired"
            t.message_post(body=_("انتهى الاشتراك."))
        reminder = self.env.ref("resta_saas_master.mail_template_trial_ending", raise_if_not_found=False)
        for t in self.search([("state", "=", "trial"), ("trial_end", "=", today + timedelta(days=3))]):
            if reminder:
                reminder.send_mail(t.id)
            if t.account_manager_id:
                t.activity_schedule("mail.mail_activity_data_call", user_id=t.account_manager_id.id,
                                    summary=_("التجربة تنتهي بعد 3 أيام - تواصل للاشتراك"))

    def action_retry_provision(self):
        self.write({"state": "provisioning", "provision_log": False})
        self.env.ref("resta_saas_master.ir_cron_resta_provision")._trigger()

    def action_suspend(self):
        self.write({"state": "suspended"})

    def action_reactivate(self):
        for t in self:
            t.state = "active" if t.subscription_end and t.subscription_end >= fields.Date.today() else "trial"

    def action_cancel(self):
        self.write({"state": "cancelled"})

    def action_extend_trial(self):
        for t in self:
            t.trial_end = max(t.trial_end or fields.Date.today(), fields.Date.today()) + timedelta(days=7)
            if t.state == "expired":
                t.state = "trial"
            t.message_post(body=_("تم تمديد التجربة 7 أيام حتى %s", t.trial_end))

    def action_open_system(self):
        self.ensure_one()
        return {"type": "ir.actions.act_url", "url": self.url, "target": "new"}

    def action_whatsapp(self):
        self.ensure_one()
        phone = re.sub(r"\D", "", self.owner_phone or "")
        return {"type": "ir.actions.act_url", "url": "https://wa.me/%s" % phone, "target": "new"}

    def action_view_feedback(self):
        return {"type": "ir.actions.act_window", "res_model": "resta.feedback", "name": _("الملاحظات"),
                "view_mode": "list,form", "domain": [("tenant_id", "in", self.ids)]}

    def action_view_invoices(self):
        return {"type": "ir.actions.act_window", "res_model": "account.move", "name": _("الفواتير"),
                "view_mode": "list,form", "domain": [("id", "in", self.subscription_ids.invoice_id.ids)]}

    # ----------------------------------------------------------------- API
    @api.model
    def _authenticate_api(self, account, token):
        tenant = self.sudo().search([("account_number", "=", str(account or ""))], limit=1)
        if not tenant or not token or not hmac.compare_digest(tenant.token or "", str(token)):
            return self.browse()
        return tenant

    def _login_signature(self, ts):
        self.ensure_one()
        msg = ("%s:%s" % (self.account_number, ts)).encode()
        return hmac.new(self.sudo().token.encode(), msg, hashlib.sha256).hexdigest()

    def login_payload(self):
        ts = str(int(time.time()))
        return {"auth_url": "%s/resta/auth" % self.url, "account": self.account_number,
                "ts": ts, "sig": self._login_signature(ts), "reset_url": "%s/web/reset_password" % self.url}

    def _sync_response(self, last_id=0):
        self.ensure_one()
        am = self.account_manager_id
        ICP = self.env["ir.config_parameter"].sudo()
        anns = self.env["resta.announcement"].sudo()._for_tenant(self, int(last_id or 0))
        return {
            "state": self.state,
            "trial_end": str(self.trial_end or ""),
            "subscription_end": str(self.subscription_end or ""),
            "days_left": self.days_left,
            "plan": self.plan_id.name or "",
            "account_manager": {
                "name": am.name or "", "email": am.email or "", "phone": am.partner_id.phone or "",
                "whatsapp": re.sub(r"\D", "", am.partner_id.phone or ICP.get_param("resta.support_whatsapp", "")),
            },
            "support": {"phone": ICP.get_param("resta.support_phone", ""),
                        "whatsapp": ICP.get_param("resta.support_whatsapp", ""),
                        "email": ICP.get_param("resta.support_email", "")},
            "announcements": [a._payload() for a in anns],
        }

    def _record_usage(self, usage):
        self.ensure_one()
        usage = usage or {}

        def dt(v):
            try:
                return fields.Datetime.to_datetime(v) if v else False
            except (ValueError, TypeError):
                return False

        self.sudo().write({
            "last_sync": fields.Datetime.now(),
            "usage_users": int(usage.get("users") or 0),
            "usage_pos": int(usage.get("pos") or 0),
            "usage_orders_30d": int(usage.get("orders_30d") or 0),
            "usage_sales_30d": float(usage.get("sales_30d") or 0.0),
            "usage_last_order": dt(usage.get("last_order")),
            "usage_last_login": dt(usage.get("last_login")),
            "usage_modules": int(usage.get("modules") or 0),
        })
