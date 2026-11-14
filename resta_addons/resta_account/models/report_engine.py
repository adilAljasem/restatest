"""Resta financial reports, written for Odoo Community.

Every report is computed from posted journal items (account.move.line) with plain SQL
aggregates, then shaped in Python into rows the screen, the Excel export and the
print view share:

    {"columns": [{"label", "date_from", "date_to"}...],
     "rows": [{"key", "name", "level", "type", "values": [...], "account_id"?, "partner_id"?,
               "expandable"?, "drill"?}...]}

Filters (options): date_from, date_to, compare, analytic_ids (branches), posted_only,
partner_type, tax_report_id, hide_zero. Companies = the companies selected in the switcher.
"""
import ast
import operator
import re
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import SQL

PL_TYPES = ("income", "income_other", "expense", "expense_other", "expense_depreciation",
            "expense_direct_cost")
NO_OPENING_TYPES = PL_TYPES + ("equity_unaffected",)

PL_SECTIONS = [
    # key, title, account types, sign (1 = shown as credit-positive)
    ("revenue", "الإيرادات", ("income",), -1),
    ("cost", "تكلفة الإيرادات", ("expense_direct_cost",), 1),
    ("gross", "مجمل الربح", None, None),
    ("opex", "المصروفات التشغيلية", ("expense",), 1),
    ("depreciation", "الإهلاك", ("expense_depreciation",), 1),
    ("operating", "الربح التشغيلي", None, None),
    ("other_income", "إيرادات أخرى", ("income_other",), -1),
    ("other_expense", "مصروفات أخرى", ("expense_other",), 1),
    ("net", "صافي الربح", None, None),
]
BS_SECTIONS = [
    ("assets", "الأصول", [
        ("cash", "النقد وما في حكمه", ("asset_cash",)),
        ("receivable", "الذمم المدينة", ("asset_receivable",)),
        ("current_assets", "أصول متداولة أخرى", ("asset_current", "asset_prepayments")),
        ("fixed", "الأصول الثابتة", ("asset_fixed",)),
        ("non_current_assets", "أصول غير متداولة", ("asset_non_current",)),
    ], 1),
    ("liabilities", "الالتزامات", [
        ("payable", "الذمم الدائنة", ("liability_payable",)),
        ("credit_card", "بطاقات الائتمان", ("liability_credit_card",)),
        ("current_liabilities", "التزامات متداولة", ("liability_current",)),
        ("non_current_liabilities", "التزامات غير متداولة", ("liability_non_current",)),
    ], -1),
    ("equity", "حقوق الملكية", [
        ("equity_accounts", "رأس المال والاحتياطيات", ("equity",)),
    ], -1),
]
AGED_BUCKETS = [("not_due", "غير مستحق"), ("b1", "1 - 30"), ("b2", "31 - 60"), ("b3", "61 - 90"),
                ("b4", "91 - 120"), ("older", "أكثر من 120"), ("total", "الإجمالي")]

REPORTS = {
    "pl": "قائمة الدخل",
    "pl_branch": "قائمة الدخل حسب الفرع",
    "bs": "الميزانية العمومية",
    "tb": "ميزان المراجعة",
    "gl": "دفتر الأستاذ العام",
    "partner": "كشف حساب العملاء والموردين",
    "aged": "أعمار الذمم",
    "tax": "إقرار ضريبة القيمة المضافة",
}

_ARITH = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}


def _eval_arith(expr):
    """Evaluate + - * / ( ) on numbers only."""
    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            v = walk(node.operand)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.BinOp) and type(node.op) in _ARITH:
            right = walk(node.right)
            if isinstance(node.op, ast.Div) and not right:
                return 0.0
            return _ARITH[type(node.op)](walk(node.left), right)
        raise ValueError("unsupported expression")
    return walk(ast.parse(expr, mode="eval"))


class RestaAccountReport(models.AbstractModel):
    _name = "resta.account.report"
    _description = "Resta financial reports"

    # ------------------------------------------------------------------ public API
    @api.model
    def get_filters_data(self):
        analytic = self.env["account.analytic.account"].search_read(
            [("company_id", "in", self.env.companies.ids + [False])], ["name", "code"], order="name")
        country_ids = self.env.companies.account_fiscal_country_id.ids
        tax_reports = self.env["account.report"].search_read(
            [("country_id", "in", country_ids), ("line_ids.expression_ids.engine", "=", "tax_tags")],
            ["name"], order="sequence, id")
        return {"analytic": analytic, "tax_reports": tax_reports, "currency": self._currency_info()}

    @api.model
    def get_report(self, kind, options=None):
        self._check_access()
        options = self._options(kind, options or {})
        method = getattr(self, "_report_%s" % kind, None)
        if kind not in self._report_kinds() or not method:
            raise UserError(_("تقرير غير معروف: %s", kind))
        result = method(options)
        result.update(kind=kind, title=self._report_kinds()[kind], options=options,
                      company=", ".join(self.env.companies.mapped("name")),
                      currency=self._currency_info())
        if options.get("hide_zero"):
            result["rows"] = [r for r in result["rows"] if r.get("type") != "account"
                              or any(abs(v or 0) > 0.0049 for v in r["values"])]
        return result

    @api.model
    def get_detail(self, kind, options, row):
        """Journal items under one row (general ledger / partner ledger / aged)."""
        self._check_access()
        options = self._options(kind, options or {})
        return getattr(self, "_detail_%s" % kind)(options, row)

    @api.model
    def _resta_rename_menus(self):
        """Community calls the app «Invoicing»; with the full features it is the accounting app."""
        menu = self.env.ref("account.menu_finance", raise_if_not_found=False)
        if menu:
            langs = {code for code, _name in self.env["res.lang"].get_installed()}
            values = {"en_US": "Accounting"}
            if "ar_001" in langs:
                values["ar_001"] = "المحاسبة"
            menu.sudo().update_field_translations("name", values)

    @api.model
    def _report_kinds(self):
        return dict(REPORTS)

    # ------------------------------------------------------------------ helpers
    def _resta_query(self, query):
        """Raw SQL sees what the ORM has not written yet only after a flush."""
        self.env.flush_all()
        return self.env.execute_query_dict(query)

    def _check_access(self):
        if not self.env.user.has_group("account.group_account_readonly") \
                and not self.env.user.has_group("account.group_account_invoice"):
            raise UserError(_("لا تملك صلاحية التقارير المالية."))

    def _currency_info(self):
        cur = self.env.company.currency_id
        return {"symbol": cur.symbol, "position": cur.position, "digits": cur.decimal_places}

    def _options(self, kind, options):
        today = fields.Date.context_today(self)
        o = dict(options)
        o["date_to"] = fields.Date.to_date(o.get("date_to")) or today
        default_from = self._fy_start(o["date_to"]) if kind in ("tb", "gl", "partner") \
            else o["date_to"].replace(day=1)
        o["date_from"] = fields.Date.to_date(o.get("date_from")) or default_from
        if o["date_from"] > o["date_to"]:
            raise UserError(_("تاريخ البداية بعد تاريخ النهاية."))
        o.setdefault("compare", "none")
        o.setdefault("posted_only", True)
        o.setdefault("hide_zero", True)
        o.setdefault("partner_type", "receivable")
        o["analytic_ids"] = [int(a) for a in o.get("analytic_ids") or []]
        o["company_ids"] = self.env.companies.ids
        o["date_to"], o["date_from"] = fields.Date.to_string(o["date_to"]), fields.Date.to_string(o["date_from"])
        return o

    def _d(self, value):
        return fields.Date.to_date(value)

    def _fy_start(self, day):
        return self.env.company.compute_fiscalyear_dates(day)["date_from"]

    def _where(self, options, date_from=None, date_to=None, before=None, types=None, exclude_types=None,
               extra=None):
        conds = [SQL("aml.company_id = ANY(%s)", options["company_ids"]), SQL("aml.account_id IS NOT NULL")]
        states = ["posted"] if options.get("posted_only", True) else ["posted", "draft"]
        conds.append(SQL("aml.parent_state = ANY(%s)", states))
        if date_from:
            conds.append(SQL("aml.date >= %s", date_from))
        if date_to:
            conds.append(SQL("aml.date <= %s", date_to))
        if before:
            conds.append(SQL("aml.date < %s", before))
        if types:
            conds.append(SQL("acc.account_type = ANY(%s)", list(types)))
        if exclude_types:
            conds.append(SQL("acc.account_type <> ALL(%s)", list(exclude_types)))
        if extra:
            conds.append(extra)
        return SQL(" AND ").join(conds)

    def _factor(self, options):
        """Share of each journal item that belongs to the selected branches (analytic)."""
        if not options.get("analytic_ids"):
            return SQL(""), SQL("1.0")
        ids = [str(i) for i in options["analytic_ids"]]
        join = SQL("""
            JOIN LATERAL (
                SELECT SUM(d.value::numeric) / 100.0 AS share
                  FROM jsonb_each_text(aml.analytic_distribution) d
                 WHERE string_to_array(d.key, ',') && %s::text[]
            ) an ON an.share IS NOT NULL""", ids)
        return join, SQL("an.share")

    def _sums(self, options, group_by=("account_id",), **where):
        """[{group fields..., debit, credit, balance}] for the filtered journal items."""
        join, share = self._factor(options)
        cols = SQL(", ").join(SQL.identifier("aml", g) for g in group_by)
        query = SQL("""
            SELECT %(cols)s,
                   SUM(aml.debit * %(share)s) AS debit,
                   SUM(aml.credit * %(share)s) AS credit,
                   SUM(aml.balance * %(share)s) AS balance
              FROM account_move_line aml
              JOIN account_account acc ON acc.id = aml.account_id
              %(join)s
             WHERE %(where)s
          GROUP BY %(cols)s""", cols=cols, share=share, join=join, where=self._where(options, **where))
        return self._resta_query(query)

    def _accounts(self, ids):
        accounts = self.env["account.account"].with_company(self.env.company).browse(ids).exists()
        return {a.id: a for a in accounts}

    def _account_label(self, account):
        return "%s %s" % (account.code or "", account.name) if account.code else account.name

    def _row(self, key, name, values, rtype="account", level=2, **extra):
        return dict(key=key, name=name, values=[round(v, 2) if isinstance(v, float) else v for v in values],
                    type=rtype, level=level, **extra)

    def _columns_for(self, options, kind):
        d_from, d_to = self._d(options["date_from"]), self._d(options["date_to"])
        cols = [{"label": "%s - %s" % (d_from, d_to) if kind == "pl" else str(d_to),
                 "date_from": options["date_from"] if kind == "pl" else False, "date_to": options["date_to"]}]
        if options.get("compare") == "previous_period":
            if kind == "pl":
                months = (d_to.year - d_from.year) * 12 + d_to.month - d_from.month + 1
                if d_from.day == 1 and (d_to + timedelta(days=1)).day == 1:
                    p_from, p_to = d_from - relativedelta(months=months), d_from - timedelta(days=1)
                else:
                    span = d_to - d_from
                    p_to = d_from - timedelta(days=1)
                    p_from = p_to - span
            else:
                p_from, p_to = False, d_to.replace(day=1) - timedelta(days=1)
            cols.append({"label": "%s - %s" % (p_from, p_to) if p_from else str(p_to),
                         "date_from": p_from and str(p_from), "date_to": str(p_to)})
        elif options.get("compare") == "previous_year":
            p_from = d_from - relativedelta(years=1) if kind == "pl" else False
            p_to = d_to - relativedelta(years=1)
            cols.append({"label": "%s - %s" % (p_from, p_to) if p_from else str(p_to),
                         "date_from": p_from and str(p_from), "date_to": str(p_to)})
        if len(cols) == 2:
            cols.append({"label": "التغيّر %", "percent": True})
        return cols

    def _with_variation(self, rows, cols):
        if not any(c.get("percent") for c in cols):
            return rows
        for r in rows:
            cur, prev = r["values"][0], r["values"][1]
            if cur is None or prev is None:
                r["values"].append(None)
            else:
                r["values"].append(round((cur - prev) / abs(prev) * 100, 1) if prev else None)
        return rows

    # ------------------------------------------------------------------ profit and loss
    def _pl_balances(self, options, date_from, date_to):
        res = {}
        for r in self._sums(options, date_from=date_from, date_to=date_to, types=PL_TYPES):
            res[r["account_id"]] = r["balance"]
        return res

    def _report_pl(self, options):
        cols = self._columns_for(options, "pl")
        periods = [c for c in cols if not c.get("percent")]
        balances = [self._pl_balances(options, c["date_from"], c["date_to"]) for c in periods]
        accounts = self._accounts({a for b in balances for a in b})
        rows, totals = [], {}
        for key, title, types, sign in PL_SECTIONS:
            if types is None:
                if key == "gross":
                    vals = [totals["revenue"][i] - totals["cost"][i] for i in range(len(periods))]
                elif key == "operating":
                    vals = [totals["gross"][i] - totals["opex"][i] - totals["depreciation"][i]
                            for i in range(len(periods))]
                else:
                    vals = [totals["operating"][i] + totals["other_income"][i] - totals["other_expense"][i]
                            for i in range(len(periods))]
                totals[key] = vals
                rows.append(self._row(key, title, vals, "total", 0))
                continue
            section_accounts = sorted((a for a in accounts.values() if a.account_type in types),
                                      key=lambda a: a.code or "")
            sub = [self._row("a%d" % a.id, self._account_label(a),
                             [sign * b.get(a.id, 0.0) for b in balances], account_id=a.id, drill=True)
                   for a in section_accounts]
            vals = [sum(r["values"][i] for r in sub) for i in range(len(periods))]
            totals[key] = vals
            rows.append(self._row(key, title, vals, "section", 1))
            rows += sub
        return {"columns": cols, "rows": self._with_variation(rows, cols)}

    # P&L with one column per branch (analytic account) + allocation hook for cost centres
    def _branch_columns(self, options):
        """Selected branches, else every analytic account used on P&L items of the period."""
        Analytic = self.env["account.analytic.account"]
        if options.get("analytic_ids"):
            return Analytic.browse(options["analytic_ids"]).exists()
        query = SQL("""
            SELECT DISTINCT unnest(string_to_array(d.key, ','))::int AS analytic_id
              FROM account_move_line aml
              JOIN account_account acc ON acc.id = aml.account_id
             CROSS JOIN LATERAL jsonb_each_text(aml.analytic_distribution) d
             WHERE aml.analytic_distribution IS NOT NULL AND %s""",
                    self._where(options, date_from=options["date_from"], date_to=options["date_to"], types=PL_TYPES))
        ids = [r["analytic_id"] for r in self._resta_query(query)]
        return Analytic.browse(ids).exists().sorted(lambda a: (a.plan_id.sequence, a.plan_id.id, a.name))

    def _report_pl_branch(self, options):
        branches = self._branch_columns(options)
        per_branch = []
        for branch in branches:
            sub_options = dict(options, analytic_ids=[branch.id])
            per_branch.append(self._pl_balances(sub_options, options["date_from"], options["date_to"]))
        total = self._pl_balances(dict(options, analytic_ids=[]), options["date_from"], options["date_to"])
        balances = per_branch + [total]
        cols = [{"label": b.name, "date_from": options["date_from"], "date_to": options["date_to"],
                 "analytic_id": b.id} for b in branches]
        cols.append({"label": "الإجمالي (كل القيود)", "date_from": options["date_from"],
                     "date_to": options["date_to"]})
        accounts = self._accounts({a for b in balances for a in b})
        rows, totals, n = [], {}, len(balances)
        for key, title, types, sign in PL_SECTIONS:
            if types is None:
                if key == "gross":
                    vals = [totals["revenue"][i] - totals["cost"][i] for i in range(n)]
                elif key == "operating":
                    vals = [totals["gross"][i] - totals["opex"][i] - totals["depreciation"][i] for i in range(n)]
                else:
                    vals = [totals["operating"][i] + totals["other_income"][i] - totals["other_expense"][i]
                            for i in range(n)]
                totals[key] = vals
                rows.append(self._row(key, title, vals, "total", 0))
                continue
            sub = [self._row("a%d" % a.id, self._account_label(a), [sign * b.get(a.id, 0.0) for b in balances],
                             account_id=a.id, drill=True)
                   for a in sorted((a for a in accounts.values() if a.account_type in types),
                                   key=lambda a: a.code or "")]
            vals = [sum(r["values"][i] for r in sub) for i in range(n)]
            totals[key] = vals
            rows.append(self._row(key, title, vals, "section", 1))
            rows += sub
        result = {"columns": cols, "rows": rows, "branches": branches.ids}
        return self._pl_branch_postprocess(options, result, branches)

    def _pl_branch_postprocess(self, options, result, branches):
        """Hook for allocations (e.g. cost centres spread over profit centres)."""
        return result

    # ------------------------------------------------------------------ balance sheet
    def _bs_values(self, options, date_to):
        d_to = self._d(date_to)
        fy_start = self._fy_start(d_to)
        balances = {r["account_id"]: r["balance"]
                    for r in self._sums(options, date_to=date_to, exclude_types=NO_OPENING_TYPES)}
        current = sum(r["balance"] for r in self._sums(options, date_from=fy_start, date_to=date_to,
                                                       types=PL_TYPES))
        previous = sum(r["balance"] for r in self._sums(options, before=fy_start, types=PL_TYPES))
        previous += sum(r["balance"] for r in self._sums(options, date_to=date_to, types=("equity_unaffected",)))
        return balances, -current, -previous

    def _report_bs(self, options):
        cols = self._columns_for(options, "bs")
        periods = [c for c in cols if not c.get("percent")]
        data = [self._bs_values(options, c["date_to"]) for c in periods]
        accounts = self._accounts({a for b, _c, _p in data for a in b})
        rows, n = [], len(periods)
        grand = {}
        for key, title, groups, sign in BS_SECTIONS:
            section_rows, section_vals = [], [0.0] * n
            for gkey, gtitle, types in groups:
                accs = sorted((a for a in accounts.values() if a.account_type in types), key=lambda a: a.code or "")
                sub = [self._row("a%d" % a.id, self._account_label(a), [sign * d[0].get(a.id, 0.0) for d in data],
                                 account_id=a.id, drill=True, level=3) for a in accs]
                gvals = [sum(r["values"][i] for r in sub) for i in range(n)]
                if sub:
                    section_rows.append(self._row(gkey, gtitle, gvals, "section", 2))
                    section_rows += sub
                section_vals = [section_vals[i] + gvals[i] for i in range(n)]
            if key == "equity":
                prev = [d[2] for d in data]
                cur = [d[1] for d in data]
                section_rows.append(self._row("retained", "أرباح مبقاة (سنوات سابقة)", prev, "account", 2))
                section_rows.append(self._row("current_earnings", "أرباح السنة الحالية", cur, "account", 2))
                section_vals = [section_vals[i] + prev[i] + cur[i] for i in range(n)]
            grand[key] = section_vals
            rows.append(self._row(key, title, section_vals, "total", 0))
            rows += section_rows
        rows.append(self._row("liab_equity", "إجمالي الالتزامات وحقوق الملكية",
                              [grand["liabilities"][i] + grand["equity"][i] for i in range(n)], "total", 0))
        return {"columns": cols, "rows": self._with_variation(rows, cols),
                "balanced": all(abs(grand["assets"][i] - grand["liabilities"][i] - grand["equity"][i]) < 0.01
                                for i in range(n))}

    # ------------------------------------------------------------------ trial balance / general ledger
    def _tb_data(self, options):
        d_from = self._d(options["date_from"])
        fy_start = self._fy_start(d_from)
        opening = {}
        for r in self._sums(options, before=options["date_from"], exclude_types=NO_OPENING_TYPES):
            opening[r["account_id"]] = r["balance"]
        if fy_start < d_from:
            for r in self._sums(options, date_from=fy_start, before=options["date_from"], types=PL_TYPES):
                opening[r["account_id"]] = r["balance"]
        undistributed = sum(r["balance"] for r in self._sums(options, before=fy_start, types=PL_TYPES))
        undistributed += sum(r["balance"] for r in self._sums(options, before=options["date_from"],
                                                               types=("equity_unaffected",)))
        moves = {r["account_id"]: r for r in self._sums(options, date_from=options["date_from"],
                                                        date_to=options["date_to"])}
        return opening, moves, undistributed

    def _report_tb(self, options, expandable=False):
        opening, moves, undistributed = self._tb_data(options)
        cols = [{"label": "الرصيد الافتتاحي", "mode": "before"}, {"label": "مدين", "mode": "period"},
                {"label": "دائن", "mode": "period"}, {"label": "الرصيد الختامي", "mode": "upto"}]
        for c in cols:
            c.update(date_from=options["date_from"], date_to=options["date_to"])
        options["fy_start"] = str(self._fy_start(self._d(options["date_from"])))
        accounts = self._accounts(set(opening) | set(moves))
        rows, totals = [], [0.0] * 4
        for a in sorted(accounts.values(), key=lambda a: a.code or ""):
            o = opening.get(a.id, 0.0)
            m = moves.get(a.id, {"debit": 0.0, "credit": 0.0, "balance": 0.0})
            vals = [o, m["debit"], m["credit"], o + m["balance"]]
            totals = [totals[i] + vals[i] for i in range(4)]
            rows.append(self._row("a%d" % a.id, self._account_label(a), vals, account_id=a.id, drill=True,
                                  expandable=expandable, level=1))
        if abs(undistributed) > 0.0049:
            rows.append(self._row("undistributed", "أرباح/خسائر سنوات سابقة غير مقفلة",
                                  [undistributed, 0.0, 0.0, undistributed], "account", 1))
            totals[0] += undistributed
            totals[3] += undistributed
        rows.append(self._row("total", "الإجمالي", totals, "total", 0))
        return {"columns": cols, "rows": rows}

    def _report_gl(self, options):
        return self._report_tb(options, expandable=True)

    def _lines(self, options, extra, limit=2000, opening=0.0):
        join, share = self._factor(options)
        query = SQL("""
            SELECT aml.id, aml.date, aml.name, aml.ref, am.name AS move_name, am.id AS move_id,
                   j.code AS journal, p.name AS partner,
                   aml.debit * %(share)s AS debit, aml.credit * %(share)s AS credit,
                   aml.balance * %(share)s AS balance
              FROM account_move_line aml
              JOIN account_account acc ON acc.id = aml.account_id
              JOIN account_move am ON am.id = aml.move_id
              JOIN account_journal j ON j.id = aml.journal_id
         LEFT JOIN res_partner p ON p.id = aml.partner_id
              %(join)s
             WHERE %(where)s
          ORDER BY aml.date, am.name, aml.id
             LIMIT %(limit)s""", share=share, join=join, limit=limit + 1,
                    where=self._where(options, date_from=options["date_from"], date_to=options["date_to"],
                                      extra=extra))
        rows = self._resta_query(query)
        running, out = opening, []
        for r in rows[:limit]:
            running += r["balance"]
            out.append({"id": r["id"], "move_id": r["move_id"], "date": str(r["date"]),
                        "move": r["move_name"], "journal": r["journal"], "partner": r["partner"] or "",
                        "label": r["name"] or r["ref"] or "", "debit": round(r["debit"], 2),
                        "credit": round(r["credit"], 2), "balance": round(running, 2)})
        return {"lines": out, "more": len(rows) > limit, "opening": round(opening, 2)}

    def _detail_gl(self, options, row):
        account_id = int(row["account_id"])
        opening, _moves, _u = self._tb_data(options)
        return self._lines(options, SQL("aml.account_id = %s", account_id), opening=opening.get(account_id, 0.0))

    # ------------------------------------------------------------------ partner ledger
    def _partner_types(self, options):
        return {"receivable": ("asset_receivable",), "payable": ("liability_payable",)}.get(
            options.get("partner_type"), ("asset_receivable", "liability_payable"))

    def _report_partner(self, options):
        types = self._partner_types(options)
        opening = {r["partner_id"]: r["balance"]
                   for r in self._sums(options, ("partner_id",), before=options["date_from"], types=types)}
        moves = {r["partner_id"]: r for r in self._sums(options, ("partner_id",), date_from=options["date_from"],
                                                        date_to=options["date_to"], types=types)}
        partners = self.env["res.partner"].browse([p for p in set(opening) | set(moves) if p]).exists()
        names = {p.id: p.display_name for p in partners}
        cols = [{"label": "الرصيد الافتتاحي", "mode": "before"}, {"label": "مدين", "mode": "period"},
                {"label": "دائن", "mode": "period"}, {"label": "الرصيد", "mode": "upto"}]
        for c in cols:
            c.update(date_from=options["date_from"], date_to=options["date_to"])
        rows, totals = [], [0.0] * 4
        for pid in sorted(set(opening) | set(moves), key=lambda p: names.get(p, "")):
            o = opening.get(pid, 0.0)
            m = moves.get(pid, {"debit": 0.0, "credit": 0.0, "balance": 0.0})
            vals = [o, m["debit"], m["credit"], o + m["balance"]]
            totals = [totals[i] + vals[i] for i in range(4)]
            rows.append(self._row("p%s" % (pid or 0), names.get(pid) or "بدون شريك", vals, partner_id=pid or False,
                                  expandable=True, level=1))
        rows.append(self._row("total", "الإجمالي", totals, "total", 0))
        return {"columns": cols, "rows": rows}

    def _detail_partner(self, options, row):
        pid = row.get("partner_id") or False
        types = self._partner_types(options)
        cond = SQL("aml.partner_id = %s", pid) if pid else SQL("aml.partner_id IS NULL")
        extra = SQL("%s AND acc.account_type = ANY(%s)", cond, list(types))
        opening = sum(r["balance"] for r in self._sums(options, ("partner_id",), before=options["date_from"],
                                                       types=types) if r["partner_id"] == pid)
        return self._lines(options, extra, opening=opening)

    # ------------------------------------------------------------------ aged receivable / payable
    def _aged_items(self, options, partner_id=None):
        types = self._partner_types(options) if options.get("partner_type") != "both" \
            else ("asset_receivable", "liability_payable")
        extra = SQL("acc.account_type = ANY(%s)", list(types))
        if partner_id is not None:
            extra = SQL("%s AND %s", extra, SQL("aml.partner_id = %s", partner_id) if partner_id
                        else SQL("aml.partner_id IS NULL"))
        as_of = options["date_to"]
        join, share = self._factor(options)
        query = SQL("""
            SELECT aml.id, aml.partner_id, aml.date, COALESCE(aml.date_maturity, aml.date) AS due,
                   am.name AS move_name, am.id AS move_id, aml.name,
                   (aml.balance
                    - COALESCE((SELECT SUM(pr.amount) FROM account_partial_reconcile pr
                                 WHERE pr.debit_move_id = aml.id AND pr.max_date <= %(as_of)s), 0)
                    + COALESCE((SELECT SUM(pr.amount) FROM account_partial_reconcile pr
                                 WHERE pr.credit_move_id = aml.id AND pr.max_date <= %(as_of)s), 0)
                   ) * %(share)s AS open_amount
              FROM account_move_line aml
              JOIN account_account acc ON acc.id = aml.account_id
              JOIN account_move am ON am.id = aml.move_id
              %(join)s
             WHERE %(where)s""", as_of=as_of, share=share, join=join,
                    where=self._where(options, date_to=as_of, extra=extra))
        items = [r for r in self._resta_query(query) if abs(r["open_amount"]) > 0.0049]
        d_to = self._d(as_of)
        for r in items:
            days = (d_to - r["due"]).days
            r["bucket"] = "not_due" if days <= 0 else "b1" if days <= 30 else "b2" if days <= 60 \
                else "b3" if days <= 90 else "b4" if days <= 120 else "older"
            r["days"] = max(days, 0)
        return items

    def _report_aged(self, options):
        items = self._aged_items(options)
        keys = [k for k, _l in AGED_BUCKETS]
        cols = [{"label": label, "date_to": options["date_to"]} for _k, label in AGED_BUCKETS]
        by_partner = {}
        for r in items:
            vals = by_partner.setdefault(r["partner_id"], dict.fromkeys(keys, 0.0))
            vals[r["bucket"]] += r["open_amount"]
            vals["total"] += r["open_amount"]
        partners = self.env["res.partner"].browse([p for p in by_partner if p]).exists()
        names = {p.id: p.display_name for p in partners}
        sign = -1 if options.get("partner_type") == "payable" else 1
        rows, totals = [], [0.0] * len(keys)
        for pid in sorted(by_partner, key=lambda p: -abs(by_partner[p]["total"])):
            vals = [sign * by_partner[pid][k] for k in keys]
            totals = [totals[i] + vals[i] for i in range(len(keys))]
            rows.append(self._row("p%s" % (pid or 0), names.get(pid) or "بدون شريك", vals,
                                  partner_id=pid or False, expandable=True, level=1))
        rows.append(self._row("total", "الإجمالي", totals, "total", 0))
        return {"columns": cols, "rows": rows}

    def _detail_aged(self, options, row):
        sign = -1 if options.get("partner_type") == "payable" else 1
        items = self._aged_items(options, row.get("partner_id") or False)
        return {"lines": [{"id": r["id"], "move_id": r["move_id"], "date": str(r["date"]), "move": r["move_name"],
                           "label": r["name"] or "", "due": str(r["due"]), "days": r["days"],
                           "bucket": r["bucket"], "amount": round(sign * r["open_amount"], 2)}
                          for r in sorted(items, key=lambda r: r["due"])], "more": False}

    # ------------------------------------------------------------------ VAT return (tax report lines of the country)
    def _report_tax(self, options):
        Report = self.env["account.report"]
        report = Report.browse(int(options["tax_report_id"])).exists() if options.get("tax_report_id") else Report
        if not report:
            report = Report.search([("country_id", "in", self.env.companies.account_fiscal_country_id.ids),
                                    ("line_ids.expression_ids.engine", "=", "tax_tags")], order="sequence, id",
                                   limit=1)
        if not report:
            return {"columns": [], "rows": [],
                    "message": _("لا يوجد نموذج إقرار ضريبي لبلد الشركة. اختر الدليل المحاسبي السعودي في الإعدادات.")}
        options["tax_report_id"] = report.id
        country = report.country_id
        join, share = self._factor(options)
        query = SQL("""
            SELECT rel.account_account_tag_id AS tag_id, SUM(aml.balance * %(share)s) AS balance
              FROM account_move_line aml
              JOIN account_account acc ON acc.id = aml.account_id
              JOIN account_account_tag_account_move_line_rel rel ON rel.account_move_line_id = aml.id
              %(join)s
             WHERE %(where)s
          GROUP BY rel.account_account_tag_id""", share=share, join=join,
                    where=self._where(options, date_from=options["date_from"], date_to=options["date_to"]))
        by_tag = {r["tag_id"]: r["balance"] for r in self._resta_query(query)}
        Tag = self.env["account.account.tag"]
        values, tags_by_line = {}, {}
        expressions = report.line_ids.expression_ids
        for expr in expressions.filtered(lambda e: e.engine == "tax_tags"):
            sign = -1 if expr.formula.strip().startswith("-") else 1
            tags = Tag._get_tax_tags(expr.formula.strip().lstrip("-"), country.id)
            values[(expr.report_line_id.code or expr.report_line_id.id, expr.label)] = \
                sign * sum(by_tag.get(t.id, 0.0) for t in tags)
            tags_by_line.setdefault(expr.report_line_id.id, set()).update(tags.ids)

        by_code = {l.code: l for l in report.line_ids if l.code}
        term = re.compile(r"([A-Za-z_][\w]*)\.([A-Za-z_]\w*)")

        def value(line, label, depth=0):
            key = (line.code or line.id, label)
            if key in values:
                return values[key]
            expr = line.expression_ids.filtered(lambda e: e.label == label)[:1]
            if not expr or expr.engine != "aggregation" or depth > 20:
                return None
            formula = term.sub(lambda m: "(%r)" % (value(by_code[m.group(1)], m.group(2), depth + 1) or 0.0)
                               if m.group(1) in by_code else "0", expr.formula)
            values[key] = _eval_arith(formula)
            return values[key]

        cols = [{"label": c.name, "expression_label": c.expression_label, "date_from": options["date_from"],
                 "date_to": options["date_to"]} for c in report.column_ids.sorted("sequence")]
        rows = []
        for line in report.line_ids.sorted(lambda l: (l.sequence, l.id)):
            vals = [value(line, c["expression_label"]) for c in cols]
            has_expr = bool(line.expression_ids)
            rows.append(self._row("l%d" % line.id, line.name, vals,
                                  "account" if has_expr else "section", 1 + (line.hierarchy_level or 0) // 2,
                                  tag_ids=sorted(tags_by_line.get(line.id, [])), drill=bool(tags_by_line.get(line.id))))
        return {"columns": cols, "rows": rows, "tax_report": report.name}
