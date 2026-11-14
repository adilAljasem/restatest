"""Budgets: what the restaurant plans to sell and spend, month by month, per branch.

A budget covers a period (usually the fiscal year) and optionally one branch (analytic
account). Each line is one P&L account for one month. The «budget vs actual» report on
the reports screen compares it with the posted figures.
"""
import calendar
from collections import defaultdict

from dateutil.relativedelta import relativedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .report_engine import PL_SECTIONS, PL_TYPES


def _months(date_from, date_to):
    """[(first day, last day)] of every month touched by the period."""
    out, day = [], date_from.replace(day=1)
    while day <= date_to:
        last = day.replace(day=calendar.monthrange(day.year, day.month)[1])
        out.append((max(day, date_from), min(last, date_to)))
        day = last + relativedelta(days=1)
    return out


class RestaBudget(models.Model):
    _name = "resta.budget"
    _description = "موازنة"
    _inherit = ["mail.thread"]
    _order = "date_from desc, id desc"
    _check_company_auto = True

    name = fields.Char("الموازنة", required=True, tracking=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda s: s.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")
    date_from = fields.Date("من", required=True, default=lambda s: s._default_dates()[0], tracking=True)
    date_to = fields.Date("إلى", required=True, default=lambda s: s._default_dates()[1], tracking=True)
    analytic_account_id = fields.Many2one("account.analytic.account", "الفرع", check_company=True, tracking=True,
                                          help="اتركه فارغاً لموازنة المطعم كاملاً.")
    state = fields.Selection([("draft", "مسودة"), ("confirmed", "معتمدة"), ("cancel", "ملغاة")],
                             string="الحالة", default="draft", required=True, tracking=True)
    line_ids = fields.One2many("resta.budget.line", "budget_id", "البنود", copy=True)
    planned_revenue = fields.Monetary("الإيرادات المخططة", compute="_compute_totals")
    planned_expense = fields.Monetary("المصروفات المخططة", compute="_compute_totals")
    planned_profit = fields.Monetary("الربح المخطط", compute="_compute_totals")
    note = fields.Html("ملاحظات")

    _date_check = models.Constraint("CHECK(date_from <= date_to)", "تاريخ البداية يجب أن يسبق النهاية.")

    def _default_dates(self):
        dates = self.env.company.compute_fiscalyear_dates(fields.Date.context_today(self))
        return dates["date_from"], dates["date_to"]

    @api.depends("line_ids.planned_amount", "line_ids.account_id.account_type")
    def _compute_totals(self):
        revenue_types = {"income", "income_other"}
        for budget in self:
            rev = sum(l.planned_amount for l in budget.line_ids if l.account_id.account_type in revenue_types)
            exp = sum(l.planned_amount for l in budget.line_ids if l.account_id.account_type not in revenue_types)
            budget.planned_revenue, budget.planned_expense, budget.planned_profit = rev, exp, rev - exp

    @api.constrains("date_from", "date_to", "line_ids")
    def _check_lines_in_period(self):
        for budget in self:
            if any(l.date_from < budget.date_from or l.date_to > budget.date_to for l in budget.line_ids):
                raise ValidationError(_("كل بنود الموازنة يجب أن تكون داخل مدتها."))

    def action_confirm(self):
        for budget in self:
            if not budget.line_ids:
                raise UserError(_("أضف بنود الموازنة أولاً (أو استخدم «تعبئة تلقائية»)."))
        self.write({"state": "confirmed"})

    def action_draft(self):
        self.write({"state": "draft"})

    def action_cancel(self):
        self.write({"state": "cancel"})

    def action_open_report(self):
        self.ensure_one()
        return {"type": "ir.actions.client", "tag": "resta_account.report", "name": _("الموازنة مقابل الفعلي"),
                "params": {"kind": "budget", "options": {"budget_id": self.id}}}

    def action_open_fill(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "resta.budget.fill", "view_mode": "form",
                "target": "new", "context": {"default_budget_id": self.id}}

    def _planned_by_account(self, date_from, date_to):
        """Planned amount per account between two dates (month lines are prorated by days)."""
        res = defaultdict(float)
        for line in self.line_ids:
            start, end = max(line.date_from, date_from), min(line.date_to, date_to)
            if start > end:
                continue
            days = (line.date_to - line.date_from).days + 1
            res[line.account_id.id] += line.planned_amount * ((end - start).days + 1) / days
        return dict(res)

    def _actual_by_account_month(self, date_from=None, date_to=None):
        """{(account_id, month first day): actual (sign of the P&L section)} for the budget's branch."""
        self.ensure_one()
        date_from, date_to = date_from or self.date_from, date_to or self.date_to
        Engine = self.env["resta.account.report"]
        options = Engine._options("pl", {"analytic_ids": self.analytic_account_id.ids,
                                         "date_from": str(date_from), "date_to": str(date_to)})
        signs = {t: sign for _k, _t, types, sign in PL_SECTIONS if types for t in types}
        res = {}
        for start, end in _months(date_from, date_to):
            balances = Engine._pl_balances(options, str(start), str(end))
            accounts = self.env["account.account"].browse(list(balances))
            for account in accounts:
                res[(account.id, start.replace(day=1))] = signs.get(account.account_type, 1) * balances[account.id]
        return res


class RestaBudgetLine(models.Model):
    _name = "resta.budget.line"
    _description = "بند موازنة"
    _order = "date_from, account_id"

    budget_id = fields.Many2one("resta.budget", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="budget_id.company_id", store=True)
    currency_id = fields.Many2one(related="budget_id.currency_id")
    state = fields.Selection(related="budget_id.state")
    account_id = fields.Many2one("account.account", "الحساب", required=True,
                                 domain=[("account_type", "in", PL_TYPES)])
    date_from = fields.Date("من", required=True)
    date_to = fields.Date("إلى", required=True)
    planned_amount = fields.Monetary("المخطط", required=True, default=0.0,
                                     help="للإيرادات: المبيعات المتوقعة. للمصروفات: الحد المسموح به.")
    actual_amount = fields.Monetary("الفعلي", compute="_compute_actual")
    achievement = fields.Float("نسبة التحقق %", compute="_compute_actual")

    _date_check = models.Constraint("CHECK(date_from <= date_to)", "تاريخ البداية يجب أن يسبق النهاية.")

    @api.depends("account_id", "date_from", "date_to", "budget_id.analytic_account_id")
    def _compute_actual(self):
        Engine = self.env["resta.account.report"]
        signs = {t: sign for _k, _t, types, sign in PL_SECTIONS if types for t in types}
        for line in self:
            if not (line.account_id and line.date_from and line.date_to):
                line.actual_amount = line.achievement = 0.0
                continue
            options = Engine._options("pl", {"analytic_ids": line.budget_id.analytic_account_id.ids,
                                             "date_from": str(line.date_from), "date_to": str(line.date_to)})
            balance = Engine._pl_balances(options, str(line.date_from), str(line.date_to)).get(line.account_id.id, 0.0)
            line.actual_amount = signs.get(line.account_id.account_type, 1) * balance
            line.achievement = round(line.actual_amount / line.planned_amount * 100, 1) if line.planned_amount else 0.0


class RestaBudgetFill(models.TransientModel):
    _name = "resta.budget.fill"
    _description = "تعبئة الموازنة"

    budget_id = fields.Many2one("resta.budget", required=True)
    method = fields.Selection([("last_year", "من فعلي السنة الماضية"), ("amount", "مبلغ سنوي يوزّع على الأشهر")],
                              string="الطريقة", default="last_year", required=True)
    growth = fields.Float("نسبة النمو %", default=10.0,
                          help="تضاف على فعلي السنة الماضية. للمصروفات اكتب نسبة الزيادة المسموحة.")
    account_ids = fields.Many2many("account.account", string="الحسابات", domain=[("account_type", "in", PL_TYPES)],
                                   help="اتركها فارغة لكل حسابات الإيرادات والمصروفات التي عليها حركة.")
    amount = fields.Monetary("المبلغ السنوي لكل حساب")
    currency_id = fields.Many2one(related="budget_id.currency_id")
    replace = fields.Boolean("استبدال البنود الحالية", default=True)

    def action_fill(self):
        self.ensure_one()
        budget = self.budget_id
        if budget.state != "draft":
            raise UserError(_("أعد الموازنة إلى مسودة لتعديل بنودها."))
        months = _months(budget.date_from, budget.date_to)
        commands = [Command.clear()] if self.replace else []
        if self.method == "amount":
            if not self.account_ids:
                raise UserError(_("اختر الحسابات التي توزّع عليها المبلغ."))
            total_days = (budget.date_to - budget.date_from).days + 1
            currency = budget.currency_id
            for account in self.account_ids:
                given = 0.0
                for index, (start, end) in enumerate(months):
                    if index == len(months) - 1:
                        share = currency.round(self.amount - given)  # the last month takes the rounding
                    else:
                        share = currency.round(self.amount * ((end - start).days + 1) / total_days)
                    given += share
                    commands.append(Command.create({"account_id": account.id, "date_from": start, "date_to": end,
                                                    "planned_amount": share}))
        else:
            actual = budget._actual_by_account_month(budget.date_from - relativedelta(years=1),
                                                     budget.date_to - relativedelta(years=1))
            factor = 1 + (self.growth or 0.0) / 100.0
            wanted = set(self.account_ids.ids)
            for start, end in months:
                key_month = (start - relativedelta(years=1)).replace(day=1)
                for (account_id, month), value in sorted(actual.items()):
                    if month != key_month or (wanted and account_id not in wanted) or abs(value) < 0.01:
                        continue
                    commands.append(Command.create({"account_id": account_id, "date_from": start, "date_to": end,
                                                    "planned_amount": round(value * factor, 2)}))
            if len(commands) <= (1 if self.replace else 0):
                raise UserError(_("لا توجد حركات في السنة الماضية لهذه الحسابات. استخدم «مبلغ سنوي» بدلاً منها."))
        budget.write({"line_ids": commands})
        return {"type": "ir.actions.act_window_close"}

