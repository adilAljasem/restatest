"""Data of the settings centre: setup checklist, one card per area, growth level.

Everything is computed defensively: a card or a checklist item appears only when its
module is installed, and links whose action does not exist are dropped.
"""
from odoo import _, api, models

SECTIONS = [
    ("ops", "التشغيل", [
        {"key": "pos", "module": "point_of_sale", "tab": "point_of_sale", "icon": "pos", "menu": "point_of_sale.menu_point_root",
         "title": "نقاط البيع والكاشير", "desc": "الفروع والطاولات، طرق الدفع، الإيصالات والطابعات",
         "links": [("نقاط البيع", "point_of_sale.action_pos_config_kanban"),
                   ("طرق الدفع", "point_of_sale.action_pos_payment_method_form"),
                   ("المنيو", "point_of_sale.product_template_action_pos_product"),
                   ("فئات المنيو", "point_of_sale.product_pos_category_action")]},
        {"key": "kds", "module": "resta_kds", "tab": "point_of_sale", "icon": "chef", "menu": "resta_kds.menu_kds_screen",
         "title": "شاشة المطبخ", "desc": "شاشات التحضير لكل قسم، أوقات التنبيه، شاشة استلام الطلبات",
         "links": [("الشاشات", "resta_kds.action_kds_display"), ("فتح شاشة المطبخ", "resta_kds.action_kds_screen")]},
        {"key": "stock", "module": "stock", "tab": "stock", "icon": "box", "menu": "stock.menu_stock_root",
         "title": "المخزون", "desc": "المستودعات، الجرد، الباركود، الدفعات وتواريخ الصلاحية",
         "links": [("المستودعات", "stock.action_warehouse_form"), ("أنواع العمليات", "stock.action_picking_type_list"),
                   ("تطبيق الباركود", "resta_barcode.action_barcode_app")]},
        {"key": "purchase", "module": "purchase", "tab": "purchase", "icon": "cart", "menu": "purchase.menu_purchase_root",
         "title": "المشتريات والموردون", "desc": "طلبات الشراء، اعتماد الطلبات، الموردون",
         "links": [("الموردون", "account.res_partner_action_supplier")]},
        {"key": "mrp", "module": "mrp", "tab": "mrp", "icon": "factory", "menu": "mrp.menu_mrp_root",
         "title": "الوصفات والتصنيع", "desc": "وصفات الأطباق والتحضيرات وتكلفة الطبق",
         "links": [("الوصفات", "mrp.mrp_bom_form_action")]},
    ]),
    ("sales", "المبيعات والتسويق", [
        {"key": "sale", "module": "sale_management", "tab": "sale_management", "icon": "bag", "menu": "sale.sale_menu_root",
         "title": "المبيعات والتموين", "desc": "عروض الأسعار وطلبات التموين وقوائم الأسعار",
         "links": [("قوائم الأسعار", "product.product_pricelist_action2")]},
        {"key": "loyalty", "module": "pos_loyalty", "tab": "point_of_sale", "icon": "gift", "menu": False,
         "title": "الولاء والعروض", "desc": "نقاط الولاء، كوبونات الخصم، بطاقات الهدايا",
         "links": [("برامج الولاء والعروض", "loyalty.loyalty_program_discount_loyalty_action")]},
    ]),
    ("finance", "المالية", [
        {"key": "account", "module": "account", "tab": "account", "icon": "invoice", "menu": "account.menu_finance",
         "title": "المحاسبة والضريبة", "desc": "الضرائب، البنوك، الفوترة الإلكترونية، المؤجلات والتحصيل",
         "links": [("الضرائب", "account.action_tax_form"), ("دليل الحسابات", "account.action_account_form"),
                   ("البنوك واليوميات", "account.action_account_journal_form"),
                   ("الفروع (مراكز التكلفة)", "analytic.action_account_analytic_account_form"),
                   ("مستويات التحصيل", "resta_account.action_followup_levels"),
                   ("الموازنات", "resta_account.action_resta_budget")]},
        {"key": "expense", "module": "hr_expense", "tab": "hr_expense", "icon": "receipt", "menu": "hr_expense.menu_hr_expense_root",
         "title": "النفقات", "desc": "مصروفات الموظفين وطرق سدادها", "links": []},
    ]),
    ("people", "الأشخاص", [
        {"key": "hr", "module": "hr", "tab": "hr", "icon": "people", "menu": "hr.menu_hr_root",
         "title": "الموظفون", "desc": "الأقسام والوظائف وساعات العمل وتنبيهات العقود",
         "links": [("الأقسام", "hr.hr_department_tree_action"), ("الوظائف", "hr.action_hr_job")]},
        {"key": "payroll", "module": "resta_payroll", "tab": "resta_payroll", "icon": "pay", "menu": "resta_payroll.menu_payroll_root",
         "title": "الرواتب والتأمينات", "desc": "حسابات الرواتب، الإضافات والخصومات، نسب التأمينات",
         "links": [("الإضافات والخصومات", "resta_payroll.action_input_type"),
                   ("نسب التأمينات", "resta_payroll.action_gosi_rate")]},
        {"key": "attendance", "module": "hr_attendance", "tab": "hr_attendance", "icon": "clock",
         "menu": "hr_attendance.menu_hr_attendance_root",
         "title": "الحضور", "desc": "طريقة تسجيل الحضور، الكشك، الساعات الإضافية", "links": []},
        {"key": "ess", "module": "bs_hr_ess_base", "tab": "bs_hr_ess_base", "icon": "people", "menu": False,
         "title": "الخدمة الذاتية للموظفين", "desc": "بوابة الموظف، الحضور بالجوال، الطلبات والموافقات", "links": []},
    ]),
    ("system", "المنشأة والنظام", [
        {"key": "company", "module": "base_setup", "tab": "general_settings", "icon": "settings", "menu": False,
         "title": "المنشأة والمستخدمون", "desc": "بيانات الشركة، المستخدمون والصلاحيات، اللغات، شكل الفواتير",
         "links": [("بيانات المنشأة", "__company__"), ("المستخدمون", "base.action_res_users"),
                   ("شكل المستندات", "web.action_base_document_layout_configurator")]},
        {"key": "theme", "module": "resta_sidebar_theme", "tab": "general_settings", "icon": "smart", "menu": False,
         "title": "الثيم والشعار", "desc": "شعار القائمة الجانبية، الوضع الداكن، أيقونات التطبيقات",
         "links": [], "anchor": "resta_theme_setting_container"},
        {"key": "growth", "module": "resta_growth", "tab": False, "icon": "learn", "menu": False,
         "title": "رحلة النمو", "desc": "مستواك الحالي، شروط المستوى التالي، والجولات الإرشادية",
         "links": [], "action": "resta_growth.action_growth_journey"},
    ]),
]


class RestaSettingsHome(models.AbstractModel):
    _name = "resta.settings.home"
    _description = "مركز الإعدادات"

    # ------------------------------------------------------------------ helpers
    @api.model
    def _installed(self):
        return set(self.env["ir.module.module"].sudo().search([("state", "=", "installed")]).mapped("name"))

    @api.model
    def _action(self, xmlid):
        if xmlid == "__company__":
            return {"type": "ir.actions.act_window", "res_model": "res.company", "res_id": self.env.company.id,
                    "views": [[False, "form"]], "target": "current"}
        record = self.env.ref(xmlid, raise_if_not_found=False)
        return xmlid if record and record._name.startswith("ir.actions") else False

    @api.model
    def _count(self, model, domain=None):
        if model not in self.env:
            return 0
        return self.env[model].sudo().search_count(domain or [])

    @api.model
    def _locked_menus(self):
        """{menu id: feature info} for the growth journey's locked features."""
        if "resta.growth.feature" not in self.env:
            return {}
        Feature = self.env["resta.growth.feature"]
        if Feature._user_bypasses_lock():
            return {}
        Menu = self.env["ir.ui.menu"].sudo().with_context(active_test=False)
        res = {}
        for feature in Feature.sudo().search([("unlocked", "=", False)], order="level_sequence, sequence"):
            info = {"name": feature.name, "level": feature.level_id.name, "level_seq": feature.level_id.sequence,
                    "teaser": feature.teaser or "", "color": feature.level_id.color or ""}
            for menu in Menu.search([("id", "child_of", feature.menu_ids.ids)]):
                res.setdefault(menu.id, info)
        return res

    # ------------------------------------------------------------------ checklist
    @api.model
    def _checklist(self, installed):
        company = self.env.company
        items = []

        def add(key, title, hint, done, action, module=None):
            if module and module not in installed:
                return
            items.append({"key": key, "title": title, "hint": hint, "done": bool(done), "action": action})

        add("company", _("بيانات المنشأة"), _("الاسم والرقم الضريبي والسجل التجاري والعنوان والشعار"),
            company.vat and company.street and company.city and company.logo, self._action("__company__"))
        if "l10n_sa_edi" in installed:
            journals = self.env["account.journal"].sudo().search([("type", "=", "sale"), ("company_id", "=", company.id)])
            ready = any(j.l10n_sa_production_csid_json for j in journals)
            add("zatca", _("الربط مع هيئة الزكاة (فاتورة)"), _("تسجيل الجهاز للفوترة الإلكترونية - المرحلة الثانية"),
                ready, self._action("account.action_account_journal_form"))
        add("branches", _("الفروع"), _("كل فرع مركز تكلفة لتقارير الأرباح حسب الفرع"),
            self._count("account.analytic.account", [("company_id", "in", [company.id, False])]),
            self._action("analytic.action_account_analytic_account_form"), "account")
        add("pos", _("نقاط البيع"), _("نقطة بيع لكل فرع أو كاشير"),
            self._count("pos.config", [("company_id", "=", company.id)]),
            self._action("point_of_sale.action_pos_config_kanban"), "point_of_sale")
        add("payments", _("طرق الدفع"), _("نقدي ومدى والبطاقات وتطبيقات التوصيل"),
            self._count("pos.payment.method", [("company_id", "=", company.id), ("is_cash_count", "=", False)]),
            self._action("point_of_sale.action_pos_payment_method_form"), "point_of_sale")
        add("menu", _("المنيو"), _("10 أصناف على الأقل بأسعارها وفئاتها"),
            self._count("product.template", [("available_in_pos", "=", True)]) >= 10,
            self._action("point_of_sale.product_template_action_pos_product"), "point_of_sale")
        add("kitchen", _("شاشة المطبخ"), _("شاشة لكل قسم: الشاورما، الحار، المشروبات..."),
            self._count("resta.kds.display"), self._action("resta_kds.action_kds_display"), "resta_kds")
        add("users", _("المستخدمون"), _("حساب لكل مدير أو محاسب يستخدم النظام"),
            self._count("res.users", [("share", "=", False), ("active", "=", True)]) >= 2,
            self._action("base.action_res_users"))
        add("bank", _("الحساب البنكي"), _("رقم الآيبان لاستيراد كشف البنك والتسوية"),
            self._count("account.journal", [("type", "=", "bank"), ("company_id", "=", company.id),
                                            ("bank_account_id", "!=", False)]),
            self._action("account.action_account_journal_form"), "account")
        add("employees", _("الموظفون"), _("بيانات الموظفين وعقودهم للرواتب والتأمينات"),
            self._count("hr.employee", [("company_id", "=", company.id)]) >= 2,
            self._action("hr.open_view_employee_list_my"), "hr")
        return items

    # ------------------------------------------------------------------ public
    @api.model
    def get_home_data(self):
        installed = self._installed()
        company = self.env.company
        checklist = self._checklist(installed)
        done = sum(1 for i in checklist if i["done"])
        locked = self._locked_menus()
        sections = []
        for skey, stitle, cards in SECTIONS:
            out = []
            for card in cards:
                if card["module"] not in installed:
                    continue
                menu = self.env.ref(card["menu"], raise_if_not_found=False) if card.get("menu") else None
                links = [{"label": label, "action": action}
                         for label, xmlid in card["links"] if (action := self._action(xmlid))]
                out.append({
                    "key": card["key"], "title": card["title"], "desc": card["desc"], "icon": card["icon"],
                    "tab": card["tab"], "anchor": card.get("anchor") or False, "links": links,
                    "action": self._action(card["action"]) if card.get("action") else False,
                    "locked": locked.get(menu.id) if menu else False,
                })
            if out:
                sections.append({"key": skey, "title": stitle, "cards": out})
        stats = [{"label": _("اكتمال التجهيز"), "value": "%d%%" % (round(done * 100 / len(checklist)) if checklist else 100)}]
        if "point_of_sale" in installed:
            stats.append({"label": _("نقاط البيع"), "value": self._count("pos.config", [("company_id", "=", company.id)])})
        stats.append({"label": _("المستخدمون"), "value": self._count("res.users", [("share", "=", False)])})
        if "hr" in installed:
            stats.append({"label": _("الموظفون"), "value": self._count("hr.employee", [("company_id", "=", company.id)])})
        growth = False
        if "resta.growth.engine" in self.env:
            try:
                badge = self.env["resta.growth.engine"].get_badge_data()
            except Exception:  # noqa: BLE001 - the settings page must open even if the journey is misconfigured
                badge = {}
            if badge.get("visible") and badge.get("level"):
                growth = {"name": badge["level"]["name"], "seq": badge["level"]["seq"],
                          "color": badge["level"]["color"], "icon": badge["level"].get("icon") or "fa-flag",
                          "progress": badge.get("progress", 0), "score": badge.get("score", {}).get("value", 0),
                          "action": self._action("resta_growth.action_growth_journey")}
        return {
            "company": {"name": company.name, "vat": company.vat or "", "city": company.city or "",
                        "logo": bool(company.logo), "id": company.id},
            "stats": stats, "checklist": checklist, "done": done, "total": len(checklist),
            "progress": round(done * 100 / len(checklist)) if checklist else 100,
            "sections": sections, "growth": growth,
            "is_admin": self.env.user.has_group("base.group_system"),
        }
