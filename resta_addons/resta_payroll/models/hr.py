from datetime import date

from odoo import api, fields, models

NEW_GOSI_LAW = date(2024, 7, 3)


class HrVersion(models.Model):
    """Saudi salary package on the employee contract (hr.version in Odoo 19)."""
    _inherit = "hr.version"

    resta_housing_allowance = fields.Monetary("بدل السكن", tracking=True, groups="hr.group_hr_manager")
    resta_transport_allowance = fields.Monetary("بدل النقل", tracking=True, groups="hr.group_hr_manager")
    resta_food_allowance = fields.Monetary("بدل الإعاشة", tracking=True, groups="hr.group_hr_manager")
    resta_other_allowance = fields.Monetary("بدلات أخرى ثابتة", tracking=True, groups="hr.group_hr_manager")
    resta_total_package = fields.Monetary("إجمالي الراتب", compute="_compute_resta_total_package",
                                          groups="hr.group_hr_manager")

    @api.depends("wage", "resta_housing_allowance", "resta_transport_allowance", "resta_food_allowance",
                 "resta_other_allowance")
    def _compute_resta_total_package(self):
        for version in self:
            version.resta_total_package = version.wage + version.resta_housing_allowance + \
                version.resta_transport_allowance + version.resta_food_allowance + version.resta_other_allowance

    def action_resta_default_allowances(self):
        """Common Saudi split: housing 25% and transport 10% of the basic salary."""
        for version in self:
            version.write({"resta_housing_allowance": round(version.wage * 0.25, 2),
                           "resta_transport_allowance": round(version.wage * 0.10, 2)})


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    resta_gosi_scheme = fields.Selection([
        ("old", "سعودي - النظام السابق (مسجل قبل 3 يوليو 2024)"),
        ("new", "سعودي - النظام الجديد (مسجل من 3 يوليو 2024)"),
        ("non_saudi", "غير سعودي (أخطار مهنية فقط)"),
        ("none", "غير مشمول"),
    ], string="التأمينات الاجتماعية", compute="_compute_resta_gosi_scheme", store=True, readonly=False,
        groups="hr.group_hr_user", tracking=True)
    resta_gosi_date = fields.Date("تاريخ أول تسجيل في التأمينات", groups="hr.group_hr_user",
                                  help="يحدد النظام (السابق أو الجديد) للموظف السعودي.")
    resta_gosi_number = fields.Char("رقم المشترك في التأمينات", groups="hr.group_hr_user")
    resta_analytic_account_id = fields.Many2one("account.analytic.account", "الفرع (مركز التكلفة)",
                                                groups="hr.group_hr_user",
                                                help="رواتب الموظف تُحمَّل على هذا الفرع في القيود.")
    resta_payment_method = fields.Selection([("bank", "تحويل بنكي"), ("cash", "نقداً")], default="bank",
                                            string="طريقة صرف الراتب", groups="hr.group_hr_user")

    # contract (hr.version) fields shown on the employee form
    resta_housing_allowance = fields.Monetary(related="version_id.resta_housing_allowance", inherited=True,
                                              readonly=False, groups="hr.group_hr_manager")
    resta_transport_allowance = fields.Monetary(related="version_id.resta_transport_allowance", inherited=True,
                                                readonly=False, groups="hr.group_hr_manager")
    resta_food_allowance = fields.Monetary(related="version_id.resta_food_allowance", inherited=True,
                                           readonly=False, groups="hr.group_hr_manager")
    resta_other_allowance = fields.Monetary(related="version_id.resta_other_allowance", inherited=True,
                                            readonly=False, groups="hr.group_hr_manager")
    resta_total_package = fields.Monetary(related="version_id.resta_total_package", inherited=True,
                                          groups="hr.group_hr_manager")

    def action_resta_default_allowances(self):
        return self.version_id.action_resta_default_allowances()

    @api.depends("country_id", "resta_gosi_date")
    def _compute_resta_gosi_scheme(self):
        for employee in self:
            if employee.country_id.code == "SA":
                registered = employee.resta_gosi_date
                employee.resta_gosi_scheme = "new" if registered and registered >= NEW_GOSI_LAW else "old"
            elif employee.country_id:
                employee.resta_gosi_scheme = "non_saudi"
            elif not employee.resta_gosi_scheme:
                employee.resta_gosi_scheme = "non_saudi"


class HrLeaveType(models.Model):
    _inherit = "hr.leave.type"

    resta_unpaid = fields.Boolean("بدون راتب", help="أيام هذه الإجازة تُخصم من الراتب.")


class RestaGosiRate(models.Model):
    """GOSI rates by scheme and date, so a law change is a new line, not a code change."""
    _name = "resta.gosi.rate"
    _description = "نسب التأمينات الاجتماعية"
    _order = "scheme, date_from desc"

    scheme = fields.Selection([("old", "سعودي - النظام السابق"), ("new", "سعودي - النظام الجديد"),
                               ("non_saudi", "غير سعودي")], required=True)
    date_from = fields.Date("من تاريخ", required=True)
    employee_rate = fields.Float("نسبة الموظف %", digits=(5, 2))
    employer_rate = fields.Float("نسبة صاحب العمل %", digits=(5, 2))
    note = fields.Char("التفصيل")

    @api.model
    def _rates(self, scheme, day):
        rate = self.search([("scheme", "=", scheme), ("date_from", "<=", day)], order="date_from desc", limit=1)
        return (rate.employee_rate, rate.employer_rate) if rate else (0.0, 0.0)


class ResCompany(models.Model):
    _inherit = "res.company"

    resta_payroll_journal_id = fields.Many2one("account.journal", "يومية الرواتب")
    resta_salary_account_id = fields.Many2one("account.account", "مصروف الرواتب الأساسية")
    resta_allowance_account_id = fields.Many2one("account.account", "مصروف البدلات")
    resta_gosi_expense_account_id = fields.Many2one("account.account", "مصروف التأمينات (حصة المنشأة)")
    resta_gosi_payable_account_id = fields.Many2one("account.account", "التأمينات المستحقة")
    resta_salary_payable_account_id = fields.Many2one("account.account", "رواتب مستحقة الدفع")
    resta_eosb_expense_account_id = fields.Many2one("account.account", "مصروف مكافأة نهاية الخدمة")
    resta_eosb_provision_account_id = fields.Many2one("account.account", "مخصص مكافأة نهاية الخدمة")
    resta_gosi_cap = fields.Monetary("الحد الأعلى لأجر الاشتراك", default=45000.0, currency_field="currency_id")
    resta_overtime_method = fields.Selection([
        ("law", "نظام العمل: أجر الساعة + 50% من الأجر الأساسي للساعة"),
        ("basic150", "150% من الأجر الأساسي للساعة"),
    ], default="law", string="حساب العمل الإضافي")
    resta_daily_divisor = fields.Integer("أيام الشهر لحساب أجر اليوم", default=30)
    resta_hours_per_day = fields.Float("ساعات العمل اليومية", default=8.0)
    resta_eosb_accrual = fields.Boolean("تكوين مخصص نهاية الخدمة شهرياً", default=True)
    resta_eosb_include_allowances = fields.Boolean("المكافأة على الراتب الشامل (الأساسي + البدلات)", default=True)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    resta_payroll_journal_id = fields.Many2one(related="company_id.resta_payroll_journal_id", readonly=False)
    resta_salary_account_id = fields.Many2one(related="company_id.resta_salary_account_id", readonly=False)
    resta_allowance_account_id = fields.Many2one(related="company_id.resta_allowance_account_id", readonly=False)
    resta_gosi_expense_account_id = fields.Many2one(related="company_id.resta_gosi_expense_account_id",
                                                    readonly=False)
    resta_gosi_payable_account_id = fields.Many2one(related="company_id.resta_gosi_payable_account_id",
                                                    readonly=False)
    resta_salary_payable_account_id = fields.Many2one(related="company_id.resta_salary_payable_account_id",
                                                      readonly=False)
    resta_eosb_expense_account_id = fields.Many2one(related="company_id.resta_eosb_expense_account_id",
                                                    readonly=False)
    resta_eosb_provision_account_id = fields.Many2one(related="company_id.resta_eosb_provision_account_id",
                                                      readonly=False)
    resta_gosi_cap = fields.Monetary(related="company_id.resta_gosi_cap", readonly=False)
    resta_overtime_method = fields.Selection(related="company_id.resta_overtime_method", readonly=False)
    resta_daily_divisor = fields.Integer(related="company_id.resta_daily_divisor", readonly=False)
    resta_hours_per_day = fields.Float(related="company_id.resta_hours_per_day", readonly=False)
    resta_eosb_accrual = fields.Boolean(related="company_id.resta_eosb_accrual", readonly=False)
    resta_eosb_include_allowances = fields.Boolean(related="company_id.resta_eosb_include_allowances",
                                                   readonly=False)
