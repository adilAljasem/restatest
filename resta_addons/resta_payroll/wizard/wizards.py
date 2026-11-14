from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models

REASONS = [
    ("termination", "إنهاء من صاحب العمل / انتهاء العقد (المادة 84)"),
    ("resignation", "استقالة الموظف (المادة 85)"),
    ("full", "استحقاق كامل (قوة قاهرة، المادة 81، أو الحالات الخاصة بالمادة 87)"),
]


def eosb_award(monthly_wage, years, reason):
    """End-of-service award under the Saudi Labor Law.

    Art. 84: half a month's wage for each of the first five years, a full month for each year
    after, fractions of a year counted pro rata.
    Art. 85 (resignation): nothing under 2 years, one third from 2 to 5 years, two thirds from
    5 to 10 years, the full award from 10 years.
    """
    first = min(years, 5.0)
    rest = max(years - 5.0, 0.0)
    full = monthly_wage / 2.0 * first + monthly_wage * rest
    if reason != "resignation":
        return full, 1.0
    if years < 2:
        return 0.0, 0.0
    if years < 5:
        return full / 3.0, 1 / 3.0
    if years < 10:
        return full * 2.0 / 3.0, 2 / 3.0
    return full, 1.0


class RestaEosb(models.TransientModel):
    _name = "resta.eosb"
    _description = "حاسبة مكافأة نهاية الخدمة"

    employee_id = fields.Many2one("hr.employee", "الموظف", required=True)
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")
    start_date = fields.Date("بداية الخدمة", compute="_compute_defaults", store=True, readonly=False)
    end_date = fields.Date("آخر يوم عمل", required=True, default=fields.Date.context_today)
    reason = fields.Selection(REASONS, "سبب انتهاء العلاقة", required=True, default="termination")
    wage = fields.Monetary("الأجر الشهري المعتمد", compute="_compute_defaults", store=True, readonly=False,
                           help="آخر أجر: الأساسي والبدلات الثابتة (حسب إعداد الشركة).")
    unpaid_days = fields.Float("أيام إجازة بدون راتب (لا تُحسب من الخدمة)")
    years = fields.Float("سنوات الخدمة", compute="_compute_award", digits=(6, 3))
    service_text = fields.Char("مدة الخدمة", compute="_compute_award")
    share = fields.Float("نسبة الاستحقاق", compute="_compute_award")
    amount = fields.Monetary("المكافأة المستحقة", compute="_compute_award")
    breakdown = fields.Text("طريقة الحساب", compute="_compute_award")

    @api.depends("employee_id")
    def _compute_defaults(self):
        for wiz in self:
            employee = wiz.employee_id.sudo()
            version = employee._get_version(wiz.end_date or fields.Date.context_today(wiz)) if employee else False
            wiz.start_date = employee._get_first_contract_date() if employee else False
            if version:
                company = wiz.company_id or employee.company_id
                wiz.wage = version.resta_total_package if company.resta_eosb_include_allowances else version.wage
            else:
                wiz.wage = 0.0

    @api.depends("start_date", "end_date", "reason", "wage", "unpaid_days")
    def _compute_award(self):
        for wiz in self:
            if not (wiz.start_date and wiz.end_date) or wiz.end_date < wiz.start_date:
                wiz.update({"years": 0.0, "amount": 0.0, "share": 0.0, "breakdown": "", "service_text": ""})
                continue
            days = (wiz.end_date - wiz.start_date).days + 1 - (wiz.unpaid_days or 0.0)
            years = max(days, 0) / 365.0
            delta = relativedelta(wiz.end_date + relativedelta(days=1), wiz.start_date)
            amount, share = eosb_award(wiz.wage, years, wiz.reason)
            first = min(years, 5.0)
            rest = max(years - 5.0, 0.0)
            text = [_("أول 5 سنوات: %(y).3f سنة × نصف راتب (%(w).2f) = %(v).2f",
                      y=first, w=wiz.wage / 2, v=wiz.wage / 2 * first)]
            if rest:
                text.append(_("ما بعد 5 سنوات: %(y).3f سنة × راتب كامل (%(w).2f) = %(v).2f",
                              y=rest, w=wiz.wage, v=wiz.wage * rest))
            if wiz.reason == "resignation":
                label = (_("لا شيء (أقل من سنتين)") if years < 2 else _("الثلث (2-5 سنوات)") if years < 5
                         else _("الثلثين (5-10 سنوات)") if years < 10 else _("كامل المكافأة (10 سنوات فأكثر)"))
                text.append(_("استقالة: يستحق %s.", label))
            wiz.update({"years": years, "amount": wiz.currency_id.round(amount) if wiz.currency_id else amount,
                        "share": share, "breakdown": "\n".join(text),
                        "service_text": _("%(y)s سنة و%(m)s شهر و%(d)s يوم", y=delta.years, m=delta.months,
                                          d=delta.days)})

    def action_add_to_payslip(self):
        self.ensure_one()
        slip = self.env["resta.payslip"].search([
            ("employee_id", "=", self.employee_id.id), ("state", "in", ("draft", "computed")),
            ("date_from", "<=", self.end_date), ("date_to", ">=", self.end_date)], limit=1)
        if not slip:
            slip = self.env["resta.payslip"].create({
                "employee_id": self.employee_id.id, "company_id": self.employee_id.company_id.id,
                "date_from": self.end_date.replace(day=1), "date_to": self.end_date + relativedelta(day=31)})
        input_type = self.env.ref("resta_payroll.input_eosb")
        slip.input_ids.filtered(lambda i: i.type_id == input_type).unlink()
        slip.input_ids = [(0, 0, {"type_id": input_type.id, "amount": self.amount,
                                  "name": _("مكافأة نهاية الخدمة (%s)", self.service_text)})]
        slip.compute_sheet()
        return {"type": "ir.actions.act_window", "res_model": "resta.payslip", "res_id": slip.id, "view_mode": "form"}


class RestaPayrollPayment(models.TransientModel):
    _name = "resta.payroll.payment"
    _description = "صرف الرواتب"

    batch_id = fields.Many2one("resta.payslip.batch", required=True)
    company_id = fields.Many2one(related="batch_id.company_id")
    journal_id = fields.Many2one("account.journal", "من حساب", required=True, check_company=True,
                                 domain="[('type', 'in', ('bank', 'cash'))]")
    date = fields.Date("تاريخ الصرف", required=True, default=fields.Date.context_today)
    amount = fields.Monetary(related="batch_id.total_net", string="المبلغ")
    currency_id = fields.Many2one(related="batch_id.currency_id")

    def action_pay(self):
        self.ensure_one()
        move = self.batch_id._register_payment(self.journal_id, self.date)
        return {"type": "ir.actions.act_window", "res_model": "account.move", "res_id": move.id, "view_mode": "form"}
