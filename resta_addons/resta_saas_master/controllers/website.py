import logging
import re
from datetime import timedelta

from odoo import fields, http
from odoo.http import request

from ..models.resta_tenant import BUSINESS_TYPES

_logger = logging.getLogger(__name__)

COUNTRIES = [  # code, dial, Arabic name
    ("SA", "966", "السعودية"), ("AE", "971", "الإمارات"), ("KW", "965", "الكويت"), ("BH", "973", "البحرين"),
    ("QA", "974", "قطر"), ("OM", "968", "عُمان"), ("EG", "20", "مصر"), ("JO", "962", "الأردن"),
]
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class RestaWebsite(http.Controller):

    def _values(self, **extra):
        values = {
            "countries": COUNTRIES,
            "business_types": [(k, l) for k, l, _e in BUSINESS_TYPES],
            "plans": request.env["resta.plan"].sudo().search([("published", "=", True)]),
            "support_phone": request.env["ir.config_parameter"].sudo().get_param("resta.support_phone", ""),
            "trial_days": request.env["ir.config_parameter"].sudo().get_param("resta.trial_days", "14"),
            "no_header": True, "no_footer": True,
        }
        values.update(extra)
        return values

    # ----------------------------------------------------------- marketing
    @http.route(["/resta", "/resta/home"], type="http", auth="public", website=True, sitemap=True)
    def landing(self, **kw):
        return request.render("resta_saas_master.page_landing", self._values())

    # -------------------------------------------------------------- signup
    @http.route("/resta/signup", type="http", auth="public", website=True, methods=["GET", "POST"], sitemap=True)
    def signup(self, **post):
        if request.httprequest.method != "POST":
            return request.render("resta_saas_master.page_signup", self._values(post={"country": "SA", "dial": "966"}, errors={}))
        errors = self._validate_signup(post)
        if errors:
            return request.render("resta_saas_master.page_signup", self._values(post=post, errors=errors))
        Tenant = request.env["resta.tenant"].sudo()
        dials = {d for _c, d, _n in COUNTRIES}
        dial = post.get("dial") if post.get("dial") in dials else \
            dict((c, d) for c, d, _n in COUNTRIES).get(post.get("country"), "966")
        phone = "+%s%s" % (dial, re.sub(r"\D", "", post["phone"]).lstrip("0"))
        tenant = Tenant.create({
            "name": post["business_name"].strip(),
            "business_type": post["business_type"],
            "owner_name": post["name"].strip(),
            "owner_email": post["email"].strip().lower(),
            "owner_phone": phone,
            "country_id": request.env["res.country"].sudo().search([("code", "=", post.get("country") or "SA")], limit=1).id,
            "signup_password_hash": Tenant.hash_password(post["password"]),
            "signup_ip": request.httprequest.remote_addr,
            "source": "signup",
        })
        tenant.message_post(body="تسجيل تجربة من الموقع (%s)" % tenant.signup_ip)
        request.env.ref("resta_saas_master.ir_cron_resta_provision").sudo()._trigger()
        return request.redirect("/resta/signup/status/%s" % tenant.access_token)

    def _validate_signup(self, post):
        errors = {}
        required = {"name": "الاسم", "phone": "رقم الجوال", "email": "البريد الإلكتروني", "password": "كلمة المرور",
                    "business_name": "اسم النشاط", "business_type": "نوع النشاط"}
        for key, label in required.items():
            if not (post.get(key) or "").strip():
                errors[key] = "%s مطلوب" % label
        if post.get("website_url"):  # honeypot
            errors["form"] = "تعذّر إكمال التسجيل."
        email = (post.get("email") or "").strip().lower()
        if email and not EMAIL_RE.match(email):
            errors["email"] = "البريد الإلكتروني غير صحيح"
        phone = re.sub(r"\D", "", post.get("phone") or "")
        if post.get("phone") and not 7 <= len(phone) <= 12:
            errors["phone"] = "رقم الجوال غير صحيح"
        if post.get("password") and len(post["password"]) < 8:
            errors["password"] = "كلمة المرور 8 أحرف على الأقل"
        if not post.get("terms"):
            errors["terms"] = "يجب الموافقة على الشروط والأحكام"
        Tenant = request.env["resta.tenant"].sudo()
        if email and Tenant.search_count([("owner_email", "=", email), ("state", "not in", ("cancelled", "failed"))]):
            errors["email"] = "يوجد حساب بهذا البريد. سجّل الدخول أو تواصل معنا."
        limit = int(request.env["ir.config_parameter"].sudo().get_param("resta.signup_per_ip", "3") or 3)
        ip = request.httprequest.remote_addr
        recent = Tenant.search_count([("signup_ip", "=", ip),
                                      ("create_date", ">=", fields.Datetime.now() - timedelta(hours=1))])
        if recent >= limit:
            errors["form"] = "عدد محاولات التسجيل كبير، حاول بعد ساعة أو تواصل معنا."
        return errors

    def _tenant_by_token(self, token):
        return request.env["resta.tenant"].sudo().search([("access_token", "=", token or "x")], limit=1)

    @http.route("/resta/signup/status/<string:token>", type="http", auth="public", website=True, sitemap=False)
    def signup_status(self, token, **kw):
        tenant = self._tenant_by_token(token)
        if not tenant:
            return request.not_found()
        return request.render("resta_saas_master.page_signup_status", self._values(tenant=tenant, token=token))

    @http.route("/resta/signup/status/<string:token>/json", type="http", auth="public", sitemap=False)
    def signup_status_json(self, token, **kw):
        tenant = self._tenant_by_token(token)
        if not tenant:
            return request.make_json_response({"error": "not_found"}, status=404)
        ready = tenant.state in ("trial", "active")
        return request.make_json_response({
            "state": tenant.state, "step": tenant.provision_step or "", "ready": ready,
            "account": tenant.account_number if ready else "",
            "login_url": "/resta/login?account=%s&email=%s" % (tenant.account_number, tenant.owner_email) if ready else "",
        })

    @http.route("/resta/terms", type="http", auth="public", website=True, sitemap=True)
    def terms(self, **kw):
        return request.render("resta_saas_master.page_terms", self._values())

    # --------------------------------------------------------------- login
    @http.route("/resta/login", type="http", auth="public", website=True, sitemap=True)
    def login(self, account=None, email=None, error=None, **kw):
        messages = {"auth": "البريد الإلكتروني أو كلمة المرور غير صحيحة.", "expired": "انتهت صلاحية الطلب، حاول مرة أخرى.",
                    "state": "الحساب غير متاح حالياً، تواصل مع مدير حسابك."}
        return request.render("resta_saas_master.page_login",
                              self._values(account=account or "", email=email or "", error=messages.get(error)))

    @http.route("/resta/login/lookup", type="http", auth="public", methods=["POST"], csrf=True, sitemap=False)
    def login_lookup(self, account=None, **kw):
        account = re.sub(r"\D", "", account or "")
        tenant = request.env["resta.tenant"].sudo().search([("account_number", "=", account)], limit=1)
        if not tenant:
            return request.make_json_response({"ok": False, "error": "رقم الحساب غير موجود."})
        if tenant.state in ("provisioning",):
            return request.make_json_response({"ok": False, "error": "نظامك قيد التجهيز، انتظر دقيقة."})
        if tenant.state in ("cancelled", "failed"):
            return request.make_json_response({"ok": False, "error": "الحساب غير متاح، تواصل مع الدعم."})
        return request.make_json_response(dict(tenant.login_payload(), ok=True))

    # ---------------------------------------------------------------- demo
    @http.route("/resta/demo", type="http", auth="public", website=True, methods=["GET", "POST"], sitemap=True)
    def demo(self, **post):
        if request.httprequest.method != "POST":
            return request.render("resta_saas_master.page_demo", self._values(post={}, errors={}))
        errors = {}
        for key, label in {"name": "الاسم", "phone": "رقم الجوال"}.items():
            if not (post.get(key) or "").strip():
                errors[key] = "%s مطلوب" % label
        if post.get("website_url"):
            errors["form"] = "تعذّر إرسال الطلب."
        if errors:
            return request.render("resta_saas_master.page_demo", self._values(post=post, errors=errors))
        request.env["resta.demo.request"].sudo().create({
            "name": post["name"].strip(), "phone": post["phone"].strip(), "email": (post.get("email") or "").strip(),
            "business_name": post.get("business_name"), "business_type": post.get("business_type") or False,
            "branches": int(post.get("branches") or 1), "city": post.get("city"), "message": post.get("message"),
        })
        return request.render("resta_saas_master.page_demo_thanks", self._values())
