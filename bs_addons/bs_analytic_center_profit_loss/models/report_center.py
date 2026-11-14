"""Profit and loss by center on the Resta report engine (resta_account, Odoo Community).

Columns: profit centers, then revenue centers, then the total. Each cost center's net cost is
spread over the profit centers in proportion to their revenue on the income accounts flagged
"Include in Revenue Allocation"."""
from odoo import _, api, models

from odoo.addons.resta_account.models.report_engine import PL_TYPES


class RestaAccountReport(models.AbstractModel):
    _inherit = "resta.account.report"

    @api.model
    def _report_kinds(self):
        kinds = super()._report_kinds()
        kinds["pl_center"] = _("قائمة الدخل حسب المراكز")
        return kinds

    def _bs_centers(self, options, center_types):
        Analytic = self.env["account.analytic.account"]
        centers = Analytic
        for center_type in center_types:
            centers |= Analytic.search([("bs_center_type", "=", center_type), "|", ("company_id", "=", False),
                                        ("company_id", "in", options["company_ids"])], order="name, id")
        return centers

    def _report_pl_center(self, options):
        centers = self._bs_centers(options, ("profit", "revenue"))
        result = self._report_pl_branch(dict(options, analytic_ids=centers.ids)) if centers else \
            self._report_pl_branch(dict(options, analytic_ids=[-1]))
        if not centers:
            # no centers: only the total column
            result["columns"] = result["columns"][-1:]
            for row in result["rows"]:
                row["values"] = row["values"][-1:]
        cols = result["columns"]
        rows = result["rows"]
        n = len(cols)
        period = {"date_from": options["date_from"], "date_to": options["date_to"]}

        def per_column(types, account_filter=None, sign=-1):
            values = []
            for col in cols:
                sub = dict(options, analytic_ids=[col["analytic_id"]]) if col.get("analytic_id") else \
                    dict(options, analytic_ids=[])
                total = 0.0
                for r in self._sums(sub, types=types, **period):
                    if account_filter is None or r["account_id"] in account_filter:
                        total += sign * r["balance"]
                values.append(total)
            return values

        net = next(r for r in rows if r["key"] == "net")["values"]
        alloc = per_column(("equity_unaffected",), sign=1)
        nepal = [net[i] - alloc[i] for i in range(n)]
        rows.append(self._row("bs_alloc", _("يُطرح: التوزيعات ويُضاف: المسحوبات"), alloc, "section", 1))
        rows.append(self._row("bs_nepal", _("صافي الربح بعد التوزيعات والمسحوبات"), nepal, "total", 0))

        cost_centers = self._bs_centers(options, ("cost",))
        if cost_centers:
            flagged = set(self.env["account.account"].search([("bs_include_in_center_allocation", "=", True)]).ids)
            revenue = per_column(("income",), account_filter=flagged)
            profit_idx = [i for i, c in enumerate(cols) if c.get("analytic_id")
                          and centers.browse(c["analytic_id"]).bs_center_type == "profit"]
            revenue_total = sum(revenue[i] for i in profit_idx)
            final = list(nepal)
            for cost_center in cost_centers:
                cost_total = sum(-r["balance"] for r in self._sums(dict(options, analytic_ids=[cost_center.id]),
                                                                  types=PL_TYPES, **period))
                values = []
                for i, col in enumerate(cols):
                    if not col.get("analytic_id"):
                        values.append(cost_total)
                    elif i in profit_idx and revenue_total:
                        values.append(revenue[i] / revenue_total * cost_total)
                    else:
                        values.append(0.0)
                final = [final[i] + values[i] for i in range(n)]
                rows.append(self._row("bs_cost_%d" % cost_center.id, cost_center.display_name, values, "account", 1))
            rows.append(self._row("bs_final", _("صافي الربح بعد تحميل مراكز التكلفة"), final, "total", 0))
        return result
