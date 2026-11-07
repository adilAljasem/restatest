from collections import defaultdict
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.tools.misc import format_date

# States that count as sales (GMV): everything except rejected / cancelled.
OPERATIONAL_STATES = (
    "received",
    "accepted",
    "preparing",
    "ready",
    "picked_up",
    "delivered",
)


class DeliveryDashboard(models.AbstractModel):
    _name = "bs.delivery.dashboard"
    _description = "Delivery Aggregator Dashboard Service"

    @api.model
    def get_dashboard_data(self, filters=None):
        if not self.env.user.has_group("bs_pos_delivery_core.group_delivery_viewer"):
            raise AccessError(_("Delivery dashboard access is required."))

        filters, companies = self._dashboard_filters(filters)
        currency_ids = companies.mapped("currency_id").ids
        if len(currency_ids) > 1:
            raise ValidationError(
                _(
                    "The delivery dashboard cannot combine companies with different "
                    "currencies. Select one company, or select companies that share "
                    "the same currency."
                )
            )
        company = companies[:1] or self.env.company
        # Every day boundary below is a *business day* (pos_business_day):
        # orders after midnight and before the company start hour belong to
        # the previous day, exactly like POS sales.
        period_start, selected_date = self._dashboard_period(filters, companies)
        month_start = selected_date.replace(day=1)
        previous_month_date = (month_start - timedelta(days=1)).replace(day=1)
        year_start = selected_date.replace(month=1, day=1)
        start_7_date = selected_date - timedelta(days=6)
        start_30_date = selected_date - timedelta(days=29)
        # Early in January the trend/comparison windows start in the previous
        # year, so they must participate in the lower query bound explicitly.
        query_start = min(year_start, start_30_date, start_7_date, previous_month_date, period_start)

        base_domain = self._dashboard_order_domain(filters, companies)
        period_domain = base_domain + self._business_range_domain(period_start, selected_date)

        order_model = self.env["bs.delivery.order"].with_context(tz=self.env.user.tz or "UTC")
        conversion_rate_cache = {}
        window_domain = base_domain + self._business_range_domain(query_start, selected_date)
        today_order_count = order_model.search_count(
            base_domain + self._business_range_domain(selected_date, selected_date)
        )
        month_order_count = order_model.search_count(
            base_domain + self._business_range_domain(month_start, selected_date)
        )

        # Aggregate before materialising records.  The dashboard refreshes every
        # 30 seconds, so loading every order since January is not viable for a
        # delivery-heavy restaurant.  Daily/currency buckets preserve the
        # conversion date while bounding the Python workload by calendar days.
        daily_totals = defaultdict(lambda: {"gmv": 0.0, "commission": 0.0, "net": 0.0})
        operational_rows = order_model._read_group(
            window_domain + [("state", "in", list(OPERATIONAL_STATES))],
            ["business_date:day", "currency_id"],
            ["amount_total:sum", "commission_amount:sum", "net_amount:sum"],
        )
        for business_day, currency, gmv, commission, net in operational_rows:
            day_date = fields.Date.to_date(business_day)
            bucket = daily_totals[day_date]
            bucket["gmv"] += self._convert_amount(
                currency, gmv, company, day_date, conversion_rate_cache
            )
            bucket["commission"] += self._convert_amount(
                currency, commission, company, day_date, conversion_rate_cache
            )
            bucket["net"] += self._convert_amount(
                currency, net, company, day_date, conversion_rate_cache
            )

        def _window_sum(key, first_day, last_day):
            return sum(
                values[key]
                for day_date, values in daily_totals.items()
                if first_day <= day_date <= last_day
            )

        today_gmv = daily_totals[selected_date]["gmv"]
        mtd_gmv = _window_sum("gmv", month_start, selected_date)
        mtd_commission = _window_sum("commission", month_start, selected_date)
        mtd_net = _window_sum("net", month_start, selected_date)
        ytd_gmv = _window_sum("gmv", year_start, selected_date)
        previous_month_gmv = _window_sum(
            "gmv", previous_month_date, month_start - timedelta(days=1)
        )
        # Reference definition: current MTD versus the whole previous month,
        # not versus the same number of days. A zero baseline yields zero.
        mom_growth_pct = (
            (mtd_gmv - previous_month_gmv) / previous_month_gmv * 100 if previous_month_gmv else 0.0
        )

        state_breakdown = {key: 0 for key in order_model._fields["state"].get_values(self.env)}
        for state, count in order_model._read_group(period_domain, ["state"], ["__count"]):
            state_breakdown[state] = count

        trend = []
        for offset in range(7):
            day_date = start_7_date + timedelta(days=offset)
            trend.append(
                {
                    "date": fields.Date.to_string(day_date),
                    "label": format_date(self.env, day_date, date_format="EEE"),
                    "amount": daily_totals[day_date]["gmv"],
                }
            )

        sales_domain = period_domain + [("state", "in", list(OPERATIONAL_STATES))]
        channels = self._dashboard_channel_sales(
            order_model, sales_domain, company, conversion_rate_cache
        )
        branches = self._dashboard_branch_sales(
            order_model, sales_domain, company, conversion_rate_cache
        )

        late_count = order_model.search_count(base_domain + [("is_late", "=", True)])
        recent = order_model.search(
            period_domain,
            order="received_at desc, id desc",
            limit=12,
        )
        period_count = sum(state_breakdown.values())
        qualifying_count = sum(state_breakdown[state] for state in OPERATIONAL_STATES)
        period_totals = {
            key: _window_sum(key, period_start, selected_date)
            for key in ("gmv", "commission", "net")
        }
        period_totals.update(
            {
                "orders": period_count,
                "sales_orders": qualifying_count,
                "average_order_value": period_totals["gmv"] / qualifying_count
                if qualifying_count
                else None,
                "rejection_pct": state_breakdown["rejected"] / period_count * 100
                if period_count
                else None,
                "cancellation_pct": state_breakdown["cancelled"] / period_count * 100
                if period_count
                else None,
                **self._dashboard_durations(period_domain),
            }
        )
        for rows in (channels, branches):
            for row in rows:
                row["share_pct"] = (
                    row["gmv"] / period_totals["gmv"] * 100 if period_totals["gmv"] else 0.0
                )
        integration_visible = self.env.user.has_group(
            "bs_pos_delivery_core.group_delivery_integration_manager"
        )
        start_hour = company._bs_business_start_hour()
        return {
            "period": {"date_from": str(period_start), "date_to": str(selected_date)},
            "business_day": {
                "start_hour": start_hour,
                "label": "%02d:%02d" % (int(start_hour), round((start_hour % 1) * 60)),
                "timezone": company._bs_business_tz().zone,
                "today": str(company._bs_current_business_date()),
            },
            "metrics": period_totals,
            "live": self._dashboard_live_summary(filters, companies, base_domain),
            "permissions": {
                "integration": integration_visible,
                "configure": self.env.user.has_group("bs_pos_delivery_core.group_delivery_manager"),
            },
            "currency": {
                "id": company.currency_id.id,
                "name": company.currency_id.name,
                "symbol": company.currency_id.symbol,
                "position": company.currency_id.position,
                "decimal_places": company.currency_id.decimal_places,
            },
            "company_scope": [
                {"id": scoped_company.id, "name": scoped_company.display_name}
                for scoped_company in companies
            ],
            "kpis": {
                "today_orders": today_order_count,
                "today_gmv": today_gmv,
                "mtd_orders": month_order_count,
                "mtd_gmv": mtd_gmv,
                "previous_month_gmv": previous_month_gmv,
                "mom_growth_pct": mom_growth_pct,
                "mom_comparable": bool(previous_month_gmv),
                "mtd_commission": mtd_commission,
                "mtd_net": mtd_net,
                "ytd_gmv": ytd_gmv,
                "late_orders": late_count,
            },
            "trend": trend,
            "state_breakdown": [
                {"state": state, "count": count} for state, count in state_breakdown.items()
            ],
            # Sales per real ordering platform (HungerStation, Jahez, Keeta…),
            # not per integration middleware such as Blend.
            "platforms": channels,
            "branches": branches,
            "queue_health": self._queue_health(companies, filters) if integration_visible else {},
            "recent_orders": [
                self._serialize_order(order, company, conversion_rate_cache) for order in recent
            ],
            "generated_at": fields.Datetime.to_string(fields.Datetime.now()),
        }

    # ------------------------------------------------------------------
    # Sales breakdowns
    # ------------------------------------------------------------------
    def _dashboard_channel_sales(self, order_model, domain, company, rate_cache):
        totals = {}
        rows = order_model._read_group(
            domain,
            ["external_source", "provider_id", "currency_id", "business_date:day"],
            ["__count", "amount_total:sum", "commission_amount:sum", "net_amount:sum"],
        )
        for source, provider, currency, business_day, count, gmv, commission, net in rows:
            key = self._channel_key(source, provider)
            bucket = totals.setdefault(
                key,
                {
                    "key": key,
                    "name": self._channel_label(key, provider),
                    "color": self._channel_color(key),
                    "provider_id": provider.id if self._channel_is_provider(key, provider) else 0,
                    "via": [],
                    "orders": 0,
                    "gmv": 0.0,
                    "commission": 0.0,
                    "net": 0.0,
                },
            )
            if provider and not self._channel_is_provider(key, provider):
                if provider.display_name not in bucket["via"]:
                    bucket["via"].append(provider.display_name)
            day_date = fields.Date.to_date(business_day)
            bucket["orders"] += count
            bucket["gmv"] += self._convert_amount(currency, gmv, company, day_date, rate_cache)
            bucket["commission"] += self._convert_amount(
                currency, commission, company, day_date, rate_cache
            )
            bucket["net"] += self._convert_amount(currency, net, company, day_date, rate_cache)
        return self._sorted_sales(totals.values())

    def _dashboard_branch_sales(self, order_model, domain, company, rate_cache):
        totals = {}
        rows = order_model._read_group(
            domain,
            ["pos_config_id", "currency_id", "business_date:day"],
            ["__count", "amount_total:sum", "commission_amount:sum", "net_amount:sum"],
        )
        for config, currency, business_day, count, gmv, commission, net in rows:
            bucket = totals.setdefault(
                config.id or 0,
                {
                    "pos_config_id": config.id or 0,
                    "name": config.display_name if config else _("Unassigned branch"),
                    "company": config.company_id.display_name if config else "",
                    "orders": 0,
                    "gmv": 0.0,
                    "commission": 0.0,
                    "net": 0.0,
                },
            )
            day_date = fields.Date.to_date(business_day)
            bucket["orders"] += count
            bucket["gmv"] += self._convert_amount(currency, gmv, company, day_date, rate_cache)
            bucket["commission"] += self._convert_amount(
                currency, commission, company, day_date, rate_cache
            )
            bucket["net"] += self._convert_amount(currency, net, company, day_date, rate_cache)
        return self._sorted_sales(totals.values())

    @staticmethod
    def _sorted_sales(rows):
        result = []
        for row in rows:
            row["average_order_value"] = row["gmv"] / row["orders"] if row["orders"] else None
            result.append(row)
        return sorted(result, key=lambda row: (-row["gmv"], -row["orders"], row["name"].casefold()))

    # ------------------------------------------------------------------
    # Business-day helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _business_range_domain(date_from, date_to):
        return [
            ("business_date", ">=", fields.Date.to_string(date_from)),
            ("business_date", "<=", fields.Date.to_string(date_to)),
        ]

    def _allowed_companies(self, requested_ids):
        # Default to the active company. Explicit scopes must be a subset of
        # the companies currently selected in Odoo, not merely all user companies.
        if not requested_ids:
            return self.env.company
        values = requested_ids if isinstance(requested_ids, (list, tuple, set)) else [requested_ids]
        try:
            requested = {int(value) for value in values}
        except (TypeError, ValueError) as error:
            raise ValidationError(_("Invalid dashboard company filter.")) from error
        allowed_ids = set(self.env.companies.ids)
        if requested - allowed_ids:
            raise AccessError(_("One or more requested dashboard companies are not available."))
        companies = self.env.companies.filtered(
            lambda allowed_company: allowed_company.id in requested
        )
        if not companies:
            raise AccessError(_("None of the requested companies is available."))
        return companies

    def _day_bounds(self, value=None, company=None):
        """UTC bounds of one *business* day (kept for backwards compatibility)."""
        company = company or self.env.company
        selected_date = (
            fields.Date.to_date(value) if value else company._bs_current_business_date()
        )
        return company._bs_business_day_bounds(selected_date)

    def _company_amount(self, order, amount, target_company, rate_cache=None):
        currency = order.currency_id or order.company_id.currency_id
        date = (
            order.business_date
            or fields.Date.to_date(order.received_at)
            or fields.Date.context_today(self)
        )
        return self._convert_amount(currency, amount, target_company, date, rate_cache)

    def _convert_amount(self, currency, amount, target_company, date, rate_cache=None):
        """Convert one already-aggregated currency/date bucket safely."""
        value = float(amount or 0.0)
        currency = currency or target_company.currency_id
        date = fields.Date.to_date(date) or fields.Date.context_today(self)
        if rate_cache is None:
            return currency._convert(value, target_company.currency_id, target_company, date)
        cache_key = (
            currency.id,
            target_company.currency_id.id,
            target_company.id,
            date,
        )
        rate = rate_cache.get(cache_key)
        if rate is None:
            rate = currency._get_conversion_rate(
                currency,
                target_company.currency_id,
                target_company,
                date,
            )
            rate_cache[cache_key] = rate
        return target_company.currency_id.round(value * rate) if value else 0.0

    def _sum_company_amount(self, orders, field_name, target_company, rate_cache=None):
        return sum(
            self._company_amount(order, order[field_name], target_company, rate_cache)
            for order in orders
        )

    def _queue_health(self, companies, filters=None):
        domain = self._dashboard_queue_domain(filters or {}, companies)
        return {
            "webhook_retry": self.env["bs.delivery.webhook.event"].search_count(domain),
            "outbox_retry": self.env["bs.delivery.outbox"].search_count(domain),
        }

    def _serialize_order(self, order, company, rate_cache=None):
        channel = self._channel_key(order.external_source, order.provider_id)
        is_provider = self._channel_is_provider(channel, order.provider_id)
        return {
            "id": order.id,
            "name": order.display_name,
            "provider": order.provider_id.display_name,
            "provider_id": order.provider_id.id,
            "channel": channel,
            "channel_name": self._channel_label(channel, order.provider_id),
            "channel_color": self._channel_color(channel),
            "channel_is_provider": is_provider,
            "customer": order.customer_name or _("Guest"),
            "branch": order.pos_config_id.display_name or "",
            "state": order.state,
            "is_late": order.is_late,
            "amount": self._company_amount(order, order.amount_total, company, rate_cache),
            "received_at": fields.Datetime.to_string(order.received_at),
            "business_date": fields.Date.to_string(order.business_date)
            if order.business_date
            else False,
        }
