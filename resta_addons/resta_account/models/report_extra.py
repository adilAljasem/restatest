"""More statements on the Resta report screen: cash flow and budget vs actual.

Cash flow (direct method): every journal entry that touches cash (cash and bank accounts,
plus the «outstanding receipts/payments» accounts used while a payment waits for the bank
statement) is classified by its other side. Because an entry is balanced, the cash it moved
equals minus the balance of its non-cash lines, so each counterpart account shows exactly
how much cash came in or went out for it. Transfers between cash accounts cancel out.
"""
from odoo import _, api, models
from odoo.tools import SQL

from .report_engine import PL_SECTIONS, PL_TYPES

CF_SECTIONS = [
    ("operating", "التدفقات النقدية من الأنشطة التشغيلية", [
        ("cf_customers", "مقبوضات من العملاء", ("asset_receivable",)),
        ("cf_sales", "مبيعات وإيرادات نقدية", ("income", "income_other")),
        ("cf_suppliers", "مدفوعات للموردين", ("liability_payable",)),
        ("cf_expenses", "مصروفات مدفوعة", ("expense", "expense_other", "expense_direct_cost",
                                          "expense_depreciation")),
        ("cf_other_current", "الضريبة والرواتب وبنود متداولة أخرى", ("asset_current", "asset_prepayments",
                                                                    "liability_current",
                                                                    "liability_credit_card", "off_balance")),
    ]),
    ("investing", "التدفقات النقدية من الأنشطة الاستثمارية", [
        ("cf_fixed", "شراء وبيع الأصول", ("asset_fixed", "asset_non_current")),
    ]),
    ("financing", "التدفقات النقدية من الأنشطة التمويلية", [
        ("cf_equity", "رأس المال والمسحوبات", ("equity", "equity_unaffected")),
        ("cf_loans", "القروض والالتزامات طويلة الأجل", ("liability_non_current",)),
    ]),
]


class RestaAccountReportExtra(models.AbstractModel):
    _inherit = "resta.account.report"

    @api.model
    def _report_kinds(self):
        kinds = super()._report_kinds()
        kinds.update(cf="قائمة التدفقات النقدية", budget="الموازنة مقابل الفعلي")
        return kinds

    @api.model
    def get_filters_data(self):
        data = super().get_filters_data()
        data["budgets"] = self.env["resta.budget"].search_read(
            [("company_id", "in", self.env.companies.ids), ("state", "!=", "cancel")],
            ["name", "date_from", "date_to", "analytic_account_id"], order="date_from desc, id desc")
        return data

    # ------------------------------------------------------------------ cash flow
    @api.model
    def _liquidity_account_ids(self, company_ids):
        companies = self.env["res.company"].browse(company_ids)
        Account = self.env["account.account"].sudo()
        accounts = Account.search([("account_type", "=", "asset_cash"), ("company_ids", "in", companies.ids)])
        journals = self.env["account.journal"].sudo().search([("type", "in", ("bank", "cash")),
                                                              ("company_id", "in", companies.ids)])
        accounts |= journals.default_account_id
        accounts |= (journals.inbound_payment_method_line_ids | journals.outbound_payment_method_line_ids
                     ).payment_account_id
        for company in companies:
            template = self.env["account.chart.template"].with_company(company)
            for ref in ("account_journal_payment_debit_account_id", "account_journal_payment_credit_account_id"):
                accounts |= template.ref(ref, raise_if_not_found=False) or Account
            accounts |= company.transfer_account_id
        if "pos.payment.method" in self.env:
            accounts |= self.env["pos.payment.method"].sudo().search(
                [("company_id", "in", companies.ids)]).outstanding_account_id
        return accounts.ids

    @api.model
    def _suspense_account_ids(self, company_ids):
        """Bank statement lines wait on the suspense account until they are reconciled."""
        companies = self.env["res.company"].browse(company_ids)
        journals = self.env["account.journal"].sudo().search([("type", "in", ("bank", "cash")),
                                                              ("company_id", "in", companies.ids)])
        return (journals.suspense_account_id | companies.account_journal_suspense_account_id).ids

    def _cash_balance(self, options, liquidity, before=None, date_to=None):
        where = self._where(options, before=before, date_to=date_to,
                            extra=SQL("aml.account_id = ANY(%s)", liquidity))
        query = SQL("""SELECT COALESCE(SUM(aml.balance), 0) AS balance
                         FROM account_move_line aml JOIN account_account acc ON acc.id = aml.account_id
                        WHERE %s""", where)
        return self._resta_query(query)[0]["balance"] or 0.0

    def _cash_flows(self, options, liquidity, date_from, date_to):
        """{account_id: cash in (+) / out (-)} for the entries that moved cash in the period."""
        cash_moves = SQL("""SELECT DISTINCT aml.move_id FROM account_move_line aml
                              JOIN account_account acc ON acc.id = aml.account_id
                             WHERE %s""", self._where(options, date_from=date_from, date_to=date_to,
                                                      extra=SQL("aml.account_id = ANY(%s)", liquidity)))
        query = SQL("""
            SELECT aml.account_id, SUM(-aml.balance) AS flow
              FROM account_move_line aml
              JOIN account_account acc ON acc.id = aml.account_id
             WHERE aml.move_id IN (%s) AND aml.account_id <> ALL(%s) AND %s
          GROUP BY aml.account_id""", cash_moves, liquidity, self._where(options))
        return {r["account_id"]: r["flow"] or 0.0 for r in self._resta_query(query)}

    def _report_cf(self, options):
        options = dict(options, analytic_ids=[])  # cash is not split by branch
        liquidity = self._liquidity_account_ids(options["company_ids"])
        cols = self._columns_for(options, "pl")
        periods = [c for c in cols if not c.get("percent")]
        flows = [self._cash_flows(options, liquidity, c["date_from"], c["date_to"]) for c in periods]
        accounts = self._accounts({a for f in flows for a in f})
        n = len(periods)
        drill_domain = [["move_id.line_ids.account_id", "in", liquidity]]
        rows, net = [], [0.0] * n
        opening = [self._cash_balance(options, liquidity, before=c["date_from"]) for c in periods]
        rows.append(self._row("cf_opening", "النقد وما في حكمه أول الفترة", opening, "total", 0))
        suspense = set(self._suspense_account_ids(options["company_ids"]))
        for key, title, groups in CF_SECTIONS:
            section_rows, section_vals = [], [0.0] * n
            if key == "operating":
                groups = groups + [("cf_unreconciled", "حركات بنكية لم تُسوَّ بعد", None)]
            for gkey, gtitle, types in groups:
                if types is None:
                    accs = [a for a in accounts.values() if a.id in suspense]
                else:
                    accs = sorted((a for a in accounts.values() if a.account_type in types and a.id not in suspense),
                                  key=lambda a: a.code or "")
                sub = [self._row("a%d" % a.id, self._account_label(a), [f.get(a.id, 0.0) for f in flows],
                                 account_id=a.id, drill=True, level=3, domain=drill_domain) for a in accs]
                gvals = [sum(r["values"][i] for r in sub) for i in range(n)]
                if sub:
                    section_rows.append(self._row(gkey, gtitle, gvals, "section", 2))
                    section_rows += sub
                section_vals = [section_vals[i] + gvals[i] for i in range(n)]
            net = [net[i] + section_vals[i] for i in range(n)]
            rows.append(self._row(key, title, section_vals, "section", 1))
            rows += section_rows
        rows.append(self._row("cf_net", "صافي التغيّر في النقد", net, "total", 0))
        closing = [self._cash_balance(options, liquidity, date_to=c["date_to"]) for c in periods]
        diff = [round(closing[i] - opening[i] - net[i], 2) for i in range(n)]
        if any(abs(d) > 0.009 for d in diff):
            rows.append(self._row("cf_diff", "فرق غير مصنّف (قيود نقدية بلا طرف آخر)", diff, "account", 1))
        rows.append(self._row("cf_closing", "النقد وما في حكمه آخر الفترة", closing, "total", 0))
        return {"columns": cols, "rows": self._with_variation(rows, cols)}

    # ------------------------------------------------------------------ budget vs actual
    def _report_budget(self, options):
        Budget = self.env["resta.budget"]
        budget = Budget.browse(int(options.get("budget_id") or 0)).exists()
        if not budget:
            budget = Budget.search([("company_id", "in", options["company_ids"]), ("state", "!=", "cancel"),
                                    ("date_from", "<=", options["date_to"]), ("date_to", ">=", options["date_from"])],
                                   order="date_from desc, id desc", limit=1)
        if not budget:
            return {"columns": [], "rows": [],
                    "message": _("لا توجد موازنة لهذه الفترة. أنشئ موازنة من المحاسبة ← الموازنات.")}
        options["budget_id"] = budget.id
        if budget.analytic_account_id:
            options["analytic_ids"] = [budget.analytic_account_id.id]
        d_from = max(self._d(options["date_from"]), budget.date_from)
        d_to = min(self._d(options["date_to"]), budget.date_to)
        if d_from > d_to:  # the chosen period is outside the budget: show the whole budget
            d_from, d_to = budget.date_from, budget.date_to
        options["date_from"], options["date_to"] = str(d_from), str(d_to)
        planned = budget._planned_by_account(d_from, d_to)
        actual = self._pl_balances(options, str(d_from), str(d_to))
        accounts = self._accounts(set(planned) | set(actual))
        cols = [{"label": "المخطط", "nodrill": True},
                {"label": "الفعلي", "date_from": str(d_from), "date_to": str(d_to)},
                {"label": "الفرق", "nodrill": True}, {"label": "نسبة التحقق %", "percent": True}]
        rows, totals = [], {}

        def values(plan, act):
            return [plan, act, act - plan, round(act / plan * 100, 1) if plan else None]

        for key, title, types, sign in PL_SECTIONS:
            if types is None:
                if key == "gross":
                    pv = [totals["revenue"][i] - totals["cost"][i] for i in range(2)]
                elif key == "operating":
                    pv = [totals["gross"][i] - totals["opex"][i] - totals["depreciation"][i] for i in range(2)]
                else:
                    pv = [totals["operating"][i] + totals["other_income"][i] - totals["other_expense"][i]
                          for i in range(2)]
                totals[key] = pv
                rows.append(self._row(key, title, values(*pv), "total", 0))
                continue
            accs = sorted((a for a in accounts.values() if a.account_type in types
                           and (a.id in planned or abs(actual.get(a.id, 0.0)) > 0.0049)), key=lambda a: a.code or "")
            sub = []
            for a in accs:
                plan, act = planned.get(a.id, 0.0), sign * actual.get(a.id, 0.0)
                row = self._row("a%d" % a.id, self._account_label(a), values(plan, act), account_id=a.id, drill=True)
                row["status"] = self._budget_status(key, plan, act)
                sub.append(row)
            pv = [sum(r["values"][0] for r in sub), sum(r["values"][1] for r in sub)]
            totals[key] = pv
            rows.append(self._row(key, title, values(*pv), "section", 1))
            rows += sub
        return {"columns": cols, "rows": rows, "budget": {"id": budget.id, "name": budget.display_name}}

    @api.model
    def _budget_status(self, section, plan, actual):
        """good / warn / bad: revenue should reach the plan, costs should stay under it."""
        if not plan:
            return "warn" if actual else ""
        ratio = actual / plan
        if section in ("revenue", "other_income"):
            return "good" if ratio >= 1 else "warn" if ratio >= 0.9 else "bad"
        return "good" if ratio <= 1 else "warn" if ratio <= 1.1 else "bad"

    @api.model
    def _pl_types(self):
        return PL_TYPES
