"""A simpler settings page for restaurants.

The settings form is assembled from every installed module. Before it is sent to the
browser we:
  - hide the settings of features that are not on the server (Enterprise «upgrade»
    switches): on a Community server they only lead to a sales pop-up;
  - move what restaurants never use (shipping carriers, foreign card terminals, LDAP...)
    to the advanced view (developer mode), where the support team still finds it;
  - order the tabs like the Resta side menu and call the accounting tab «المحاسبة».
"""
import re

from odoo import api, models

APP_ORDER = [
    "general_settings", "point_of_sale", "stock", "stock_request", "purchase", "mrp", "sale_management",
    "account", "hr_expense", "resta_payroll", "hr", "hr_attendance", "bs_hr_ess_base",
]
APP_LAST = ["calendar", "resta_growth"]
ADVANCED_APPS = {"calendar"}

# blocks (id or name) and settings (id) for the advanced view only
ADVANCED_BLOCKS = {
    # general settings
    "iap_purchases_setting_container", "certificates_and_keys_settings", "integration", "performance",
    "about_setting_container", "discuss", "user_default_rights", "pwa_settings",
    # sales / purchase / inventory: shipping carriers and connectors
    "sale_shipping_setting_container", "connectors_setting_container", "request_vendor_setting_container",
    "shipping_setting_container", "shipping_connectors_setting_container", "schedule_info",
    # accounting
    "peppol", "account_digitalization", "storno", "quick_edit_mode", "account_reports_settings",
    "print_vendor_checks_setting_container", "bank_cash",
}
ADVANCED_SETTINGS = {
    "eu_service", "vies_service_setting", "intrastat_statistics", "default_incoterm", "collect_customer_payment",
    "sepa_payments", "print_checks", "pos_pricer", "process_stock_barcodelookup", "email_template",
    "amazon_connector", "gelato", "shopee_connector", "qr_code_invoices", "tax_exigibility",
}
# card terminals not sold in Saudi Arabia (mada terminals come with their own Resta modules)
FOREIGN_TERMINALS = re.compile(
    r"^module_pos_(adyen|stripe|viva_com|razorpay|mercado_pago|pine_labs|paytm|qfpay|dpopay|six|worldline|"
    r"tyro|glory_cash|iot|pricer)$")

# Settings of Resta / Basic Solution modules that have no Arabic translation yet
AR_TERMS = {
    "Kitchen Flow Diagnostics": "تشخيص سرعة المطبخ",
    "Store exact timings for each kitchen display, printing and synchronization operation.":
        "تسجيل التوقيت الدقيق لكل شاشة مطبخ وطباعة ومزامنة.",
    "Kitchen Sending Status": "حالة الإرسال للمطبخ",
    "Show sending, duplicate-click prevention and completion messages.":
        "عرض رسائل الإرسال ومنع الضغط المزدوج وتأكيد الوصول.",
    "Kitchen Delivery Priority": "أولوية الإرسال للمطبخ",
    "Keep Odoo's printer-first flow, send to the display first, or run both in parallel.":
        "الطابعة أولاً، أو الشاشة أولاً، أو الاثنتان معاً.",
    "ZATCA reporting in background": "إرسال الفواتير لهيئة الزكاة في الخلفية",
    "Print the receipt instantly: simplified (B2C) invoices are signed and get their QR code at checkout, then are "
    "reported to ZATCA in the background. B2B invoices still use real-time clearance.":
        "طباعة الإيصال فوراً: الفواتير المبسطة تُوقّع ويُضاف رمز QR عند الدفع ثم تُرسل للهيئة في الخلفية. "
        "الفواتير الضريبية للمنشآت تبقى بالاعتماد الفوري.",
    "Bilingual Custom Receipt": "إيصال ثنائي اللغة",
    "Configure the bilingual Arabic/English receipt, ZATCA QR and footer message.":
        "إعداد الإيصال العربي/الإنجليزي ورمز QR للهيئة ورسالة أسفل الإيصال.",
    "Receipt Brand Name": "اسم العلامة في الإيصال",
    "Receipt Branch Name": "اسم الفرع في الإيصال",
    "Thank-you Message": "رسالة الشكر",
    "Changes apply to the Arabic thermal receipt design. Reload the Point of Sale after saving.":
        "تُطبّق التغييرات على تصميم الإيصال الحراري العربي. أعد تحميل نقطة البيع بعد الحفظ.",
    "POS Analytic Tags": "الفرع (مركز التكلفة) لنقطة البيع",
    "Analytic Account": "الحساب التحليلي",
    "Track costs and revenues in each POS sessions": "تتبّع إيرادات وتكاليف كل جلسة في فرعها",
    "Order Invoicing": "فوترة الطلبات",
    "Always To Invoice": "فاتورة لكل طلب دائماً",
    "Automatically check Invoice for every order and prevent the cashier from disabling it.":
        "تفعيل الفاتورة تلقائياً لكل طلب ومنع الكاشير من إلغائها.",
    "Disable Automatic Invoice PDF Download": "عدم فتح ملف الفاتورة تلقائياً",
    "Keep invoice creation unchanged, but do not automatically download or open the full invoice PDF after payment "
    "validation.": "تُنشأ الفاتورة كالمعتاد لكن بدون تنزيل ملف PDF أو فتحه بعد الدفع.",
    "Default Customer": "العميل الافتراضي",
    "Select this customer automatically on each new order. The cashier can replace it.":
        "يُختار هذا العميل تلقائياً في كل طلب جديد، ويمكن للكاشير تغييره.",
    "Default Customer in POS Order": "العميل الافتراضي في نقطة البيع",
    "Set a default customer for POS orders.": "عميل يُختار تلقائياً لطلبات نقطة البيع.",
    "POS Button Layout Configuration": "ترتيب أزرار نقطة البيع",
    "Button Layout": "عمود الأزرار",
    "Show the POS action buttons in a dedicated column and choose its position and number of columns":
        "عرض أزرار الإجراءات في عمود مستقل واختيار مكانه وعدد أعمدته",
    "Bonat Integration": "الربط مع بونات (الولاء)",
    "Preparation Summary": "ملخص التحضير",
    "Gulf Cooperation Council Format": "صيغة دول الخليج",
    "Add Arabic as a secondary language to your receipt": "إضافة العربية لغة ثانية في الإيصال",
}


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    @api.model
    def _get_view(self, view_id=None, view_type="form", **options):
        arch, view = super()._get_view(view_id, view_type, **options)
        if view_type == "form":
            self._resta_simplify_arch(arch)
        return arch, view

    @api.model
    def _resta_available_modules(self):
        return set(self.env["ir.module.module"].sudo().search([]).mapped("name"))

    @api.model
    def _resta_simplify_arch(self, arch):
        available = self._resta_available_modules()
        advanced = "base.group_no_one"
        for setting in list(arch.iter("setting")):
            fields = [f.get("name") or "" for f in setting.iter("field")]
            upsell = [f for f in setting.iter("field") if f.get("widget") == "upgrade_boolean"
                      and (f.get("name") or "").startswith("module_")
                      and f.get("name")[len("module_"):] not in available]
            if upsell:
                setting.set("invisible", "1")
            elif setting.get("id") in ADVANCED_SETTINGS or any(FOREIGN_TERMINALS.match(f) for f in fields):
                setting.set("groups", advanced)
        for block in arch.iter("block"):
            if (block.get("id") or block.get("name")) in ADVANCED_BLOCKS:
                block.set("groups", advanced)
                continue
            settings = list(block.iter("setting"))
            if settings and all(s.get("invisible") == "1" or s.get("groups") == advanced for s in settings):
                # nothing left for a restaurant owner in this block
                if any(s.get("invisible") != "1" for s in settings):
                    block.set("groups", advanced)
                else:
                    block.set("invisible", "1")
        apps = list(arch.iter("app"))
        for app in apps:
            if app.get("name") in ADVANCED_APPS:
                app.set("groups", advanced)
            if app.get("name") == "account" and "resta.account.report" in self.env:
                app.set("string", "المحاسبة")
                app.set("data-string", "المحاسبة")
        self._resta_order_apps(apps)
        if (self.env.lang or "").startswith("ar"):
            self._resta_translate(arch)
        return arch

    @api.model
    def _resta_translate(self, arch):
        for el in arch.iter():
            if not isinstance(el.tag, str):
                continue
            for attr in ("title", "string", "help"):
                value = el.get(attr)
                if value and " ".join(value.split()) in AR_TERMS:
                    el.set(attr, AR_TERMS[" ".join(value.split())])
            if el.text and el.text.strip() and " ".join(el.text.split()) in AR_TERMS:
                el.text = AR_TERMS[" ".join(el.text.split())]

    @api.model
    def _resta_order_apps(self, apps):
        if len(apps) < 2:
            return
        parent = apps[0].getparent()
        siblings = [a for a in apps if a.getparent() is parent]
        position = {name: i for i, name in enumerate(APP_ORDER)}
        last = {name: i for i, name in enumerate(APP_LAST)}

        def key(item):
            index, app = item
            name = app.get("name")
            if name in position:
                return (0, position[name], index)
            if name in last:
                return (2, last[name], index)
            return (1, 0, index)

        ordered = [app for _i, app in sorted(enumerate(siblings), key=key)]
        if ordered == siblings:
            return
        anchor = siblings[0].getprevious()
        for app in siblings:
            parent.remove(app)
        insert_at = parent.index(anchor) + 1 if anchor is not None else 0
        for offset, app in enumerate(ordered):
            parent.insert(insert_at + offset, app)
