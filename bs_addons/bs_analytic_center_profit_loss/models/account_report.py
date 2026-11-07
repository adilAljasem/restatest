from odoo import _, models
from odoo.tools import SQL


COST_CENTER_ACCOUNT_TYPES = (
    "income",
    "income_other",
    "expense",
    "expense_other",
    "expense_direct_cost",
    "expense_depreciation",
)
ANALYTIC_COLUMN_OPTION_KEYS = {
    "analytic_groupby_option",
    "analytic_accounts_list",
    "analytic_plan_id",
}
NON_PERIOD_OPTION_KEYS = {
    "budget_base",
    "no_subheader_division",
}


class AccountReport(models.Model):
    _inherit = "account.report"

    def _bs_is_profit_loss_by_center(self):
        self.ensure_one()
        report = self.env.ref(
            "bs_analytic_center_profit_loss.profit_loss_by_center",
            raise_if_not_found=False,
        )
        return bool(report and self == report)

    def _bs_get_report_centers(self, center_types):
        self.ensure_one()
        company_ids = self.env.context.get("bs_report_company_ids") or self.env.companies.ids
        center_ids = []
        for center_type in center_types:
            centers = self.env["account.analytic.account"].search(
                [
                    ("bs_center_type", "=", center_type),
                    "|",
                    ("company_id", "=", False),
                    ("company_id", "in", company_ids),
                ],
                order="name, id",
            )
            center_ids.extend(centers.ids)
        return self.env["account.analytic.account"].browse(center_ids)

    def _init_options_analytic_groupby(self, options, previous_options):
        if not self._bs_is_profit_loss_by_center():
            return super()._init_options_analytic_groupby(options, previous_options)

        company_ids = self.get_report_company_ids(options)
        report = self.with_context(bs_report_company_ids=company_ids)
        centers = report._bs_get_report_centers(("profit", "revenue"))

        options["analytic_accounts_groupby"] = centers.ids
        options["selected_analytic_account_groupby_names"] = centers.mapped("name")
        options["analytic_plans_groupby"] = []
        options["selected_analytic_plan_groupby_names"] = []
        options["include_analytic_without_aml"] = False
        options["display_analytic_groupby"] = False
        options["display_analytic_plan_groupby"] = False

        if centers:
            self._create_column_analytic(options)
        else:
            options["column_headers"].append([{"name": _("Total")}])


class ProfitLossByCenterReportHandler(models.AbstractModel):
    _name = "bs.profit.loss.by.center.report.handler"
    _inherit = "account.report.custom.handler"
    _description = "Profit and Loss by Center Report Handler"

    @staticmethod
    def _bs_freeze(value):
        if isinstance(value, dict):
            return tuple(
                (key, ProfitLossByCenterReportHandler._bs_freeze(item))
                for key, item in sorted(value.items())
            )
        if isinstance(value, (list, tuple)):
            return tuple(ProfitLossByCenterReportHandler._bs_freeze(item) for item in value)
        return value

    def _bs_period_signature(self, forced_options):
        return self._bs_freeze({
            key: value
            for key, value in forced_options.items()
            if key not in ANALYTIC_COLUMN_OPTION_KEYS | NON_PERIOD_OPTION_KEYS
            and key not in {"compute_budget", "budget_percentage"}
        })

    def _bs_get_center_totals(self, report, options, cost_centers):
        totals_by_signature = {}
        totals_by_column_group = {}

        for column_group_key, group_options in report._split_options_per_column_group(options).items():
            if group_options.get("compute_budget") or group_options.get("budget_percentage"):
                totals_by_column_group[column_group_key] = {}
                continue

            signature = self._bs_period_signature(
                options["column_groups"][column_group_key]["forced_options"]
            )
            if signature not in totals_by_signature:
                cost_options = {
                    **group_options,
                    "analytic_groupby_option": True,
                    "analytic_accounts_list": tuple(cost_centers.ids),
                }
                query = report._get_report_query(
                    cost_options,
                    "strict_range",
                    domain=[
                        ("account_id.account_type", "in", COST_CENTER_ACCOUNT_TYPES),
                    ],
                )
                signed_balance = report._currency_table_apply_rate(
                    SQL("-account_move_line.balance")
                )
                rows = self.env.execute_query(SQL(
                    """
                    SELECT
                        CAST(account_move_line.analytic_distribution #>> '{}' AS INTEGER) AS analytic_account_id,
                        COALESCE(SUM(%(signed_balance)s), 0.0) AS balance
                    FROM %(table_references)s
                    %(currency_table_join)s
                    WHERE %(search_condition)s
                    GROUP BY account_move_line.analytic_distribution
                    """,
                    signed_balance=signed_balance,
                    table_references=query.from_clause,
                    currency_table_join=report._currency_table_aml_join(cost_options),
                    search_condition=query.where_clause,
                ))
                totals = {analytic_account_id: balance for analytic_account_id, balance in rows}
                totals_by_signature[signature] = totals

            totals_by_column_group[column_group_key] = totals_by_signature[signature]

        return totals_by_column_group

    def _bs_get_allocation_revenue(
        self,
        report,
        options,
        visible_center_types,
    ):
        revenue_by_column_group = {}

        for column_group_key, group_options in report._split_options_per_column_group(
            options
        ).items():
            analytic_account_ids = group_options.get("analytic_accounts_list", ())
            is_profit_center_column = (
                len(analytic_account_ids) == 1
                and visible_center_types.get(analytic_account_ids[0]) == "profit"
            )
            if (
                not is_profit_center_column
                or group_options.get("compute_budget")
                or group_options.get("budget_percentage")
            ):
                revenue_by_column_group[column_group_key] = 0.0
                continue

            query = report._get_report_query(
                group_options,
                "strict_range",
                domain=[
                    ("account_id.account_type", "=", "income"),
                    ("account_id.bs_include_in_center_allocation", "=", True),
                ],
            )
            signed_revenue = report._currency_table_apply_rate(
                SQL("-account_move_line.balance")
            )
            rows = self.env.execute_query(SQL(
                """
                SELECT COALESCE(SUM(%(signed_revenue)s), 0.0) AS balance
                FROM %(table_references)s
                %(currency_table_join)s
                WHERE %(search_condition)s
                """,
                signed_revenue=signed_revenue,
                table_references=query.from_clause,
                currency_table_join=report._currency_table_aml_join(group_options),
                search_condition=query.where_clause,
            ))
            revenue_by_column_group[column_group_key] = rows[0][0]

        return revenue_by_column_group

    def _custom_line_postprocessor(self, report, options, lines):
        cost_centers = report.with_context(
            bs_report_company_ids=report.get_report_company_ids(options)
        )._bs_get_report_centers(("cost",))
        if not cost_centers:
            return lines

        try:
            net_profit_line = report._get_line_from_xml_id(
                lines,
                "bs_analytic_center_profit_loss.profit_loss_by_center_net_profit_after_allocations",
            )
        except StopIteration:
            return lines

        visible_center_ids = {
            center.id: center.bs_center_type
            for center in self.env["account.analytic.account"].browse(
                options.get("analytic_accounts_groupby", [])
            )
        }
        allocation_revenue_by_column_group = self._bs_get_allocation_revenue(
            report,
            options,
            visible_center_ids,
        )
        revenue_sum_by_signature = {}
        for column in options["columns"]:
            column_group_key = column["column_group_key"]
            forced_options = options["column_groups"][column_group_key]["forced_options"]
            analytic_account_ids = forced_options.get("analytic_accounts_list", ())
            if (
                len(analytic_account_ids) == 1
                and visible_center_ids.get(analytic_account_ids[0]) == "profit"
                and not forced_options.get("compute_budget")
                and not forced_options.get("budget_percentage")
            ):
                signature = self._bs_period_signature(forced_options)
                revenue_sum_by_signature[signature] = (
                    revenue_sum_by_signature.get(signature, 0.0)
                    + allocation_revenue_by_column_group[column_group_key]
                )

        center_totals = self._bs_get_center_totals(report, options, cost_centers)
        allocation_lines = []
        for cost_center in cost_centers:
            columns = []
            for column in options["columns"]:
                column_group_key = column["column_group_key"]
                forced_options = options["column_groups"][column_group_key]["forced_options"]
                cost_total = center_totals.get(column_group_key, {}).get(cost_center.id, 0.0)
                analytic_account_ids = forced_options.get("analytic_accounts_list", ())

                if forced_options.get("compute_budget") or forced_options.get("budget_percentage"):
                    value = 0.0
                elif analytic_account_ids:
                    analytic_account_id = analytic_account_ids[0] if len(analytic_account_ids) == 1 else False
                    if visible_center_ids.get(analytic_account_id) == "profit":
                        revenue_total = revenue_sum_by_signature.get(
                            self._bs_period_signature(forced_options),
                            0.0,
                        )
                        revenue_value = allocation_revenue_by_column_group.get(
                            column_group_key,
                            0.0,
                        )
                        value = revenue_value / revenue_total * cost_total if revenue_total else 0.0
                    else:
                        value = 0.0
                else:
                    value = cost_total

                columns.append(report._build_column_dict(value, column, options=options))

            allocation_lines.append({
                "id": report._get_generic_line_id(
                    "account.analytic.account",
                    cost_center.id,
                    markup="bs_cost_center_allocation",
                ),
                "name": cost_center.display_name,
                "columns": columns,
                "level": 1,
            })

        net_profit_after_cost_centers_columns = []
        for column_index, column in enumerate(options["columns"]):
            net_profit_value = net_profit_line["columns"][column_index].get("no_format") or 0.0
            cost_center_sum = sum(
                allocation_line["columns"][column_index].get("no_format") or 0.0
                for allocation_line in allocation_lines
            )
            net_profit_after_cost_centers_columns.append(
                report._build_column_dict(
                    net_profit_value + cost_center_sum,
                    column,
                    options=options,
                )
            )

        net_profit_after_cost_centers_line = {
            "id": report._get_generic_line_id(
                None,
                None,
                markup="bs_net_profit_after_cost_centers",
            ),
            "name": _("Net Profit Left After Cost Center"),
            "columns": net_profit_after_cost_centers_columns,
            "level": 0,
            "class": "total",
        }

        return lines + allocation_lines + [net_profit_after_cost_centers_line]
