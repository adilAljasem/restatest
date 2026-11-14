"""Saudi payroll for restaurants on Odoo Community.

A payslip is computed from the employee's contract (basic + fixed allowances), the month's
unpaid leave, the inputs entered for the month (overtime hours, bonuses, absence days,
penalties, loan installments...) and GOSI. Batches post one journal entry per month.
"""
from collections import defaultdict

from dateutil.relativedelta import relativedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_is_zero

ALLOWANCES = [
    ("HOUSING", "بدل السكن", "resta_housing_allowance"),
    ("TRANSPORT", "بدل النقل", "resta_transport_allowance"),
    ("FOOD", "بدل الإعاشة", "resta_food_allowance"),
    ("OTHER_FIXED", "بدلات أخرى", "resta_other_allowance"),
]
CATEGORIES = [
    ("basic", "الأساسي"), ("allowance", "بدل"), ("addition", "إضافة"), ("deduction", "خصم"),
    ("gosi_employee", "تأمينات (حصة الموظف)"), ("net", "صافي الراتب"),
    ("gosi_employer", "تأمينات (حصة المنشأة)"), ("eosb", "مخصص نهاية الخدمة"),
]
EARNING = ("basic", "allowance", "addition")


class RestaPayslipInputType(models.Model):
    _name = "resta.payslip.input.type"
    _description = "بند متغير في الراتب"
    _order = "sequence, id"

    name = fields.Char("البند", required=True, translate=True)
    code = fields.Char("الرمز", required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    kind = fields.Selection([("addition", "إضافة"), ("deduction", "خصم")], required=True, default="addition")
    unit = fields.Selection([("amount", "مبلغ"), ("overtime_hours", "ساعات عمل إضافي"),
                             ("absence_days", "أيام غياب")], required=True, default="amount",
                            help="ساعات العمل الإضافي وأيام الغياب تُحسب قيمتها من راتب الموظف.")
    account_id = fields.Many2one("account.account", "الحساب",
                                 help="فارغ: الإضافات على حساب البدلات، والخصومات تخفض مصروف الرواتب.")
    company_id = fields.Many2one("res.company")

    _code_uniq = models.Constraint("unique(code, company_id)", "رمز البند مستخدم.")


class RestaPayslipInput(models.Model):
    _name = "resta.payslip.input"
    _description = "بند متغير في قسيمة"
    _order = "sequence, id"

    slip_id = fields.Many2one("resta.payslip", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    type_id = fields.Many2one("resta.payslip.input.type", "البند", required=True)
    kind = fields.Selection(related="type_id.kind")
    unit = fields.Selection(related="type_id.unit")
    quantity = fields.Float("الكمية", help="عدد الساعات أو الأيام")
    amount = fields.Monetary("المبلغ")
    currency_id = fields.Many2one(related="slip_id.currency_id")
    name = fields.Char("البيان")
    account_id = fields.Many2one("account.account", "الحساب",
                                 help="فارغ: حساب البند. مثال: أقساط السلف تُقيد على حساب سلف الموظفين.")
    source_ref = fields.Char("المصدر", readonly=True, copy=False,
                             help="مرجع تقني للبنود التي أنشأتها موديولات أخرى (سلفة، طلب الموظف…).")


class RestaPayslipLine(models.Model):
    _name = "resta.payslip.line"
    _description = "سطر قسيمة راتب"
    _order = "sequence, id"

    slip_id = fields.Many2one("resta.payslip", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer()
    code = fields.Char("الرمز")
    name = fields.Char("البند")
    category = fields.Selection(CATEGORIES, "النوع")
    quantity = fields.Float("الكمية")
    amount = fields.Monetary("المبلغ")
    currency_id = fields.Many2one(related="slip_id.currency_id")
    account_id = fields.Many2one("account.account")


class RestaPayslip(models.Model):
    _name = "resta.payslip"
    _description = "قسيمة راتب"
    _inherit = ["mail.thread"]
    _order = "date_to desc, employee_id"
    _check_company_auto = True

    name = fields.Char("القسيمة", compute="_compute_name", store=True)
    employee_id = fields.Many2one("hr.employee", "الموظف", required=True, check_company=True, index=True,
                                  tracking=True)
    version_id = fields.Many2one("hr.version", "العقد")
    batch_id = fields.Many2one("resta.payslip.batch", "دفعة الرواتب", ondelete="cascade", index=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda s: s.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")
    date_from = fields.Date("من", required=True,
                            default=lambda s: fields.Date.context_today(s).replace(day=1))
    date_to = fields.Date("إلى", required=True,
                          default=lambda s: fields.Date.context_today(s) + relativedelta(day=31))
    state = fields.Selection([("draft", "مسودة"), ("computed", "محسوبة"), ("confirmed", "معتمدة"),
                              ("paid", "مصروفة"), ("cancel", "ملغاة")], string="الحالة", default="draft", tracking=True)
    paid_days = fields.Float("أيام الاستحقاق", readonly=True)
    unpaid_leave_days = fields.Float("إجازات بدون راتب", readonly=True)
    input_ids = fields.One2many("resta.payslip.input", "slip_id", "البنود المتغيرة", copy=True)
    line_ids = fields.One2many("resta.payslip.line", "slip_id", "تفاصيل الراتب", readonly=True)
    gross = fields.Monetary("إجمالي المستحقات", compute="_compute_totals", store=True)
    deductions = fields.Monetary("إجمالي الخصومات", compute="_compute_totals", store=True)
    net = fields.Monetary("صافي الراتب", compute="_compute_totals", store=True, tracking=True)
    employer_cost = fields.Monetary("تكلفة المنشأة", compute="_compute_totals", store=True)
    move_id = fields.Many2one("account.move", "قيد الرواتب", readonly=True, copy=False)
    published = fields.Boolean("منشورة للموظف", copy=False, tracking=True)
    note = fields.Text("ملاحظات")

    @api.depends("employee_id", "date_to")
    def _compute_name(self):
        for slip in self:
            slip.name = "%s - %s" % (slip.employee_id.name or "", slip.date_to and slip.date_to.strftime("%m/%Y") or "")

    @api.depends("line_ids.amount", "line_ids.category")
    def _compute_totals(self):
        for slip in self:
            lines = slip.line_ids
            slip.gross = sum(lines.filtered(lambda l: l.category in EARNING).mapped("amount"))
            slip.deductions = -sum(lines.filtered(lambda l: l.category in ("deduction", "gosi_employee"))
                                   .mapped("amount"))
            slip.net = sum(lines.filtered(lambda l: l.category == "net").mapped("amount"))
            slip.employer_cost = slip.gross + sum(lines.filtered(
                lambda l: l.category in ("gosi_employer", "eosb")).mapped("amount"))

    @api.constrains("date_from", "date_to")
    def _check_dates(self):
        for slip in self:
            if slip.date_from > slip.date_to:
                raise ValidationError(_("تاريخ البداية بعد النهاية."))

    # ------------------------------------------------------------------ computation
    def _paid_days(self, version):
        divisor = self.company_id.resta_daily_divisor or 30
        start = max(self.date_from, version.contract_date_start or self.date_from)
        end = min(self.date_to, version.contract_date_end or self.date_to)
        if start > end:
            return 0.0
        if start == self.date_from and end == self.date_to:
            return float(divisor)
        return float(min((end - start).days + 1, divisor))

    def _unpaid_leave_days(self):
        if "hr.leave" not in self.env:
            return 0.0
        leaves = self.env["hr.leave"].sudo().search([
            ("employee_id", "=", self.employee_id.id), ("state", "=", "validate"),
            ("holiday_status_id.resta_unpaid", "=", True),
            ("request_date_from", "<=", self.date_to), ("request_date_to", ">=", self.date_from)])
        days = 0.0
        for leave in leaves:
            start = max(leave.request_date_from, self.date_from)
            end = min(leave.request_date_to, self.date_to)
            span = (leave.request_date_to - leave.request_date_from).days + 1
            overlap = (end - start).days + 1
            days += leave.number_of_days * overlap / span if span else 0.0
        return round(days, 2)

    def _service_years(self, day):
        first = self.employee_id._get_first_contract_date() or self.version_id.contract_date_start
        if not first:
            return 0.0
        return max((day - first).days + 1, 0) / 365.0

    def _resta_collect_inputs(self):
        """Hook: other modules (loans, employee requests) add their inputs here."""
        return True

    def compute_sheet(self):
        for slip in self.filtered(lambda s: s.state in ("draft", "computed")):
            slip._resta_collect_inputs()
            slip._compute_sheet_lines()
            slip.state = "computed"
        return True

    def _compute_sheet_lines(self):
        self.ensure_one()
        company = self.company_id
        version = self.employee_id.sudo()._get_version(self.date_to)
        if not version or not version.contract_date_start:
            raise UserError(_("لا يوجد عقد ساري للموظف %s.", self.employee_id.name))
        self.version_id = version
        cur = self.currency_id
        divisor = company.resta_daily_divisor or 30
        hours = company.resta_hours_per_day or 8.0
        paid_days = self._paid_days(version)
        ratio = paid_days / divisor
        unpaid = min(self._unpaid_leave_days(), paid_days)
        package = version.resta_total_package
        daily = package / divisor
        hourly_total = daily / hours
        hourly_basic = version.wage / divisor / hours
        ot_rate = 1.5 * hourly_basic if company.resta_overtime_method == "basic150" \
            else hourly_total + 0.5 * hourly_basic

        lines, seq = [], 0

        def add(code, name, category, amount, quantity=0.0, account=None):
            nonlocal seq
            amount = cur.round(amount)
            if float_is_zero(amount, precision_rounding=cur.rounding):
                return
            seq += 10
            lines.append({"slip_id": self.id, "sequence": seq, "code": code, "name": name, "category": category,
                          "amount": amount, "quantity": quantity, "account_id": account and account.id})

        add("BASIC", _("الراتب الأساسي"), "basic", version.wage * ratio, paid_days, company.resta_salary_account_id)
        for code, label, field in ALLOWANCES:
            add(code, label, "allowance", version[field] * ratio, paid_days, company.resta_allowance_account_id)
        if unpaid:
            add("UNPAID", _("إجازة بدون راتب"), "deduction", -daily * unpaid, unpaid, company.resta_salary_account_id)

        for inp in self.input_ids:
            t = inp.type_id
            if t.unit == "overtime_hours":
                amount = inp.quantity * ot_rate
                inp.amount = cur.round(amount)
            elif t.unit == "absence_days":
                amount = inp.quantity * daily
                inp.amount = cur.round(amount)
            else:
                amount = inp.amount
            sign = 1 if t.kind == "addition" else -1
            default_account = company.resta_allowance_account_id if t.kind == "addition" \
                else company.resta_salary_account_id
            add(t.code, inp.name or t.name, "addition" if t.kind == "addition" else "deduction", sign * abs(amount),
                inp.quantity, inp.account_id or t.account_id or default_account)

        scheme = self.employee_id.sudo().resta_gosi_scheme or "none"
        emp_rate, er_rate = self.env["resta.gosi.rate"].sudo()._rates(scheme, self.date_to) \
            if scheme != "none" else (0.0, 0.0)
        gosi_base = min(version.wage + version.resta_housing_allowance, company.resta_gosi_cap or 45000.0) * ratio
        add("GOSI_EMP", _("التأمينات الاجتماعية (%s%%)", emp_rate), "gosi_employee", -gosi_base * emp_rate / 100.0,
            account=company.resta_gosi_payable_account_id)

        net = sum(l["amount"] for l in lines if l["category"] in EARNING + ("deduction", "gosi_employee"))
        add("NET", _("صافي الراتب"), "net", net, account=company.resta_salary_payable_account_id)
        add("GOSI_ER", _("التأمينات - حصة المنشأة (%s%%)", er_rate), "gosi_employer", gosi_base * er_rate / 100.0,
            account=company.resta_gosi_expense_account_id)
        if company.resta_eosb_accrual:
            eosb_wage = package if company.resta_eosb_include_allowances else version.wage
            years = self._service_years(self.date_to)
            monthly = eosb_wage / 12.0 if years > 5 else eosb_wage / 24.0
            add("EOSB", _("مخصص مكافأة نهاية الخدمة"), "eosb", monthly * ratio,
                account=company.resta_eosb_expense_account_id)
        self.line_ids.unlink()
        self.env["resta.payslip.line"].create(lines)
        self.write({"paid_days": paid_days, "unpaid_leave_days": unpaid})

    # ------------------------------------------------------------------ workflow
    def action_confirm(self):
        slips = self.filtered(lambda s: s.state in ("draft", "computed"))
        slips.filtered(lambda s: s.state == "draft").compute_sheet()
        for batch in slips.batch_id:
            batch.action_confirm()
        standalone = slips.filtered(lambda s: not s.batch_id)
        if standalone:
            move = standalone._resta_create_move(_("رواتب"), max(standalone.mapped("date_to")))
            standalone.write({"state": "confirmed", "move_id": move.id})
            standalone._resta_on_confirm()
        return True

    def _resta_on_confirm(self):
        """Hook: e.g. mark loan installments as paid by payroll."""
        return True

    def _resta_on_cancel(self):
        return True

    def action_cancel(self):
        for slip in self:
            if slip.state == "paid":
                raise UserError(_("لا يمكن إلغاء قسيمة مصروفة."))
            if slip.move_id and not slip.batch_id:
                slip.move_id.button_cancel()
        self._resta_on_cancel()
        self.write({"state": "cancel", "published": False})

    def action_draft(self):
        for slip in self:
            if slip.move_id and slip.move_id.state == "posted":
                raise UserError(_("القسيمة لها قيد مرحّل. ألغِ القيد أو الدفعة أولاً."))
        self.write({"state": "draft"})

    def _resta_check_accounts(self):
        company = self.company_id[:1] or self.env.company
        missing = [label for field, label in [
            ("resta_payroll_journal_id", _("يومية الرواتب")), ("resta_salary_account_id", _("مصروف الرواتب")),
            ("resta_allowance_account_id", _("مصروف البدلات")), ("resta_salary_payable_account_id", _("رواتب مستحقة")),
            ("resta_gosi_payable_account_id", _("التأمينات المستحقة")),
            ("resta_gosi_expense_account_id", _("مصروف التأمينات")),
        ] if not company[field]]
        if company.resta_eosb_accrual and not (company.resta_eosb_expense_account_id
                                               and company.resta_eosb_provision_account_id):
            missing.append(_("حسابات مخصص نهاية الخدمة"))
        if missing:
            raise UserError(_("أكمل إعدادات الرواتب (الرواتب ← الإعدادات): %s", "، ".join(missing)))
        return company

    def _resta_create_move(self, ref, day):
        company = self._resta_check_accounts()
        agg = defaultdict(float)  # (account, partner, analytic) -> balance
        for slip in self:
            partner = slip.employee_id.sudo().work_contact_id.id
            analytic = slip.employee_id.sudo().resta_analytic_account_id.id
            for line in slip.line_ids:
                amt = line.amount
                if line.category in EARNING:
                    agg[(line.account_id.id, False, analytic)] += amt
                elif line.category == "deduction":
                    # e.g. a loan installment on a receivable account keeps the employee as partner
                    tracked = line.account_id.reconcile or line.account_id.account_type in (
                        "asset_receivable", "liability_payable")
                    agg[(line.account_id.id, partner if tracked else False, False if tracked else analytic)] += amt
                elif line.category == "gosi_employee":
                    agg[(company.resta_gosi_payable_account_id.id, False, False)] += amt
                elif line.category == "net":
                    agg[(company.resta_salary_payable_account_id.id, partner, False)] -= amt
                elif line.category == "gosi_employer":
                    agg[(company.resta_gosi_expense_account_id.id, False, analytic)] += amt
                    agg[(company.resta_gosi_payable_account_id.id, False, False)] -= amt
                elif line.category == "eosb":
                    agg[(company.resta_eosb_expense_account_id.id, False, analytic)] += amt
                    agg[(company.resta_eosb_provision_account_id.id, False, False)] -= amt
        cur = company.currency_id
        move_lines = []
        for (account, partner, analytic), balance in agg.items():
            balance = cur.round(balance)
            if float_is_zero(balance, precision_rounding=cur.rounding):
                continue
            move_lines.append(Command.create({
                "name": ref, "account_id": account, "partner_id": partner,
                "debit": balance if balance > 0 else 0.0, "credit": -balance if balance < 0 else 0.0,
                "analytic_distribution": {str(analytic): 100} if analytic else False}))
        move = self.env["account.move"].sudo().create({
            "move_type": "entry", "journal_id": company.resta_payroll_journal_id.id, "date": day, "ref": ref,
            "line_ids": move_lines})
        move.action_post()
        return move

    def action_print(self):
        return self.env.ref("resta_payroll.action_report_payslip").report_action(self)


class RestaPayslipBatch(models.Model):
    _name = "resta.payslip.batch"
    _description = "دفعة رواتب"
    _inherit = ["mail.thread"]
    _order = "date_to desc, id desc"
    _check_company_auto = True

    name = fields.Char("الدفعة", required=True,
                       default=lambda s: _("رواتب %s", fields.Date.context_today(s).strftime("%m/%Y")))
    company_id = fields.Many2one("res.company", required=True, default=lambda s: s.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")
    date_from = fields.Date("من", required=True, default=lambda s: fields.Date.context_today(s).replace(day=1))
    date_to = fields.Date("إلى", required=True,
                          default=lambda s: fields.Date.context_today(s) + relativedelta(day=31))
    state = fields.Selection([("draft", "مسودة"), ("computed", "محسوبة"), ("confirmed", "معتمدة"),
                              ("paid", "مصروفة"), ("cancel", "ملغاة")], string="الحالة", default="draft", tracking=True)
    slip_ids = fields.One2many("resta.payslip", "batch_id", "القسائم")
    slip_count = fields.Integer(compute="_compute_totals")
    total_gross = fields.Monetary("إجمالي المستحقات", compute="_compute_totals")
    total_net = fields.Monetary("إجمالي الصافي", compute="_compute_totals")
    total_cost = fields.Monetary("تكلفة المنشأة", compute="_compute_totals")
    move_id = fields.Many2one("account.move", "قيد الرواتب", readonly=True, copy=False)
    payment_move_id = fields.Many2one("account.move", "قيد الصرف", readonly=True, copy=False)
    department_id = fields.Many2one("hr.department", "القسم", help="فارغ: كل الموظفين")

    @api.depends("slip_ids.net", "slip_ids.gross", "slip_ids.employer_cost", "slip_ids.state")
    def _compute_totals(self):
        for batch in self:
            slips = batch.slip_ids.filtered(lambda s: s.state != "cancel")
            batch.slip_count = len(slips)
            batch.total_gross = sum(slips.mapped("gross"))
            batch.total_net = sum(slips.mapped("net"))
            batch.total_cost = sum(slips.mapped("employer_cost"))

    def action_generate(self):
        for batch in self:
            if batch.state not in ("draft", "computed"):
                raise UserError(_("الدفعة معتمدة."))
            domain = [("company_id", "=", batch.company_id.id)]
            if batch.department_id:
                domain.append(("department_id", "child_of", batch.department_id.id))
            employees = self.env["hr.employee"].search(domain)
            existing = batch.slip_ids.employee_id
            vals = []
            for employee in employees - existing:
                version = employee.sudo()._get_version(batch.date_to)
                if not version or not version.contract_date_start or not version.wage:
                    continue
                if version.contract_date_start > batch.date_to or \
                        (version.contract_date_end and version.contract_date_end < batch.date_from):
                    continue
                vals.append({"employee_id": employee.id, "batch_id": batch.id, "company_id": batch.company_id.id,
                             "date_from": batch.date_from, "date_to": batch.date_to, "version_id": version.id})
            self.env["resta.payslip"].create(vals)
        return self.action_compute()

    def action_compute(self):
        for batch in self:
            batch.slip_ids.filtered(lambda s: s.state in ("draft", "computed")).compute_sheet()
            batch.state = "computed"
        return True

    def action_confirm(self):
        for batch in self.filtered(lambda b: b.state in ("draft", "computed")):
            slips = batch.slip_ids.filtered(lambda s: s.state in ("draft", "computed"))
            if not slips:
                raise UserError(_("لا توجد قسائم في الدفعة."))
            slips.filtered(lambda s: s.state == "draft").compute_sheet()
            move = slips._resta_create_move(batch.name, batch.date_to)
            slips.write({"state": "confirmed", "move_id": move.id})
            batch.write({"state": "confirmed", "move_id": move.id})
            slips._resta_on_confirm()
        return True

    def action_cancel(self):
        for batch in self:
            if batch.state == "paid":
                raise UserError(_("الدفعة مصروفة."))
            if batch.move_id:
                batch.move_id.button_draft()
                batch.move_id.button_cancel()
            batch.slip_ids._resta_on_cancel()
            batch.slip_ids.write({"state": "cancel", "published": False})
            batch.state = "cancel"

    def action_draft(self):
        self.filtered(lambda b: b.state == "cancel").write({"state": "draft"})
        self.slip_ids.filtered(lambda s: s.state == "cancel").write({"state": "draft", "move_id": False})
        self.filtered(lambda b: b.state == "draft").write({"move_id": False})

    def action_open_payment(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "resta.payroll.payment", "view_mode": "form",
                "target": "new", "context": {"default_batch_id": self.id}}

    def _register_payment(self, journal, day):
        self.ensure_one()
        if self.state != "confirmed":
            raise UserError(_("اعتمد الدفعة أولاً."))
        company = self.company_id
        payable = company.resta_salary_payable_account_id
        open_lines = self.move_id.line_ids.filtered(lambda l: l.account_id == payable and not l.reconciled)
        if not open_lines:
            raise UserError(_("لا توجد رواتب مستحقة في القيد."))
        lines = [Command.create({"name": _("صرف %s", self.name), "account_id": l.account_id.id,
                                 "partner_id": l.partner_id.id, "debit": -l.amount_residual, "credit": 0.0})
                 for l in open_lines]
        total = -sum(open_lines.mapped("amount_residual"))
        lines.append(Command.create({"name": _("صرف %s", self.name), "account_id": journal.default_account_id.id,
                                     "debit": 0.0, "credit": total}))
        move = self.env["account.move"].create({"move_type": "entry", "journal_id": journal.id, "date": day,
                                                "ref": _("صرف %s", self.name), "line_ids": lines})
        move.action_post()
        for line in open_lines:
            counterpart = move.line_ids.filtered(lambda m: m.account_id == payable and m.partner_id == line.partner_id
                                                 and not m.reconciled)[:1]
            (line + counterpart).reconcile()
        self.write({"state": "paid", "payment_move_id": move.id})
        self.slip_ids.filtered(lambda s: s.state == "confirmed").write({"state": "paid"})
        return move

    def action_print(self):
        return self.env.ref("resta_payroll.action_report_payslip").report_action(
            self.slip_ids.filtered(lambda s: s.state != "cancel"))

    def action_bank_sheet(self):
        self.ensure_one()
        return {"type": "ir.actions.act_url", "target": "self",
                "url": "/resta_payroll/bank_sheet/%d" % self.id}

    def action_open_slips(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "resta.payslip", "name": self.name,
                "view_mode": "list,form", "domain": [("batch_id", "=", self.id)],
                "context": {"default_batch_id": self.id, "default_date_from": str(self.date_from),
                            "default_date_to": str(self.date_to)}}
