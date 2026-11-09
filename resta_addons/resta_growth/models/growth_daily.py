"""Daily facts: one row per company per local (Riyadh) day.

The nightly cron aggregates the raw data of the restaurant (POS orders, sessions,
payments, invoices, stock moves) into these rows. Conditions, the discipline score
and the data-quality index are all computed from these rows, never from the raw
tables when a page opens. The same rows are what phase 3 sends to the platform hub
(aggregates only, no customer data).
"""
import logging
from collections import defaultdict
from datetime import datetime, time, timedelta

import pytz

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

VALID_ORDER_STATES = ("paid", "done", "invoiced")


class RestaGrowthDaily(models.Model):
    _name = "resta.growth.daily"
    _description = "حقائق يومية لرحلة النمو"
    _order = "date desc"

    company_id = fields.Many2one("res.company", required=True, index=True, ondelete="cascade",
                                 default=lambda s: s.env.company)
    date = fields.Date(required=True, index=True)
    planned = fields.Boolean("يوم تشغيل مخطط")
    recorded = fields.Boolean("يوم مسجّل", help="طلبات مدفوعة في جلسة مغلقة")
    orders = fields.Integer("الطلبات")
    sales_total = fields.Float("المبيعات شامل الضريبة")
    sales_untaxed = fields.Float("المبيعات قبل الضريبة")
    cash_sales = fields.Float("المبيعات النقدية")
    refunds_total = fields.Float("المرتجعات")
    backdated_orders = fields.Integer("طلبات بتاريخ رجعي")
    sessions_closed = fields.Integer("جلسات مغلقة")
    sessions_late = fields.Integer("جلسات أُغلقت متأخرة")
    sessions_cash_checked = fields.Integer("جلسات بعدّ نقدي")
    sessions_cash_ok = fields.Integer("جلسات مطابقة")
    cash_diff_abs = fields.Float("مجموع فروقات الصندوق المطلقة")
    invoices_total = fields.Integer("الفواتير")
    invoices_sent = fields.Integer("فواتير مقبولة/مُبلّغ عنها")
    invoices_pending_48h = fields.Integer("فواتير معلقة أكثر من 48 ساعة")
    consumption_value = fields.Float("قيمة الاستهلاك")
    waste_value = fields.Float("قيمة الهدر (الإتلاف)")
    inventory_count = fields.Boolean("تم جرد")
    inventory_coverage = fields.Float("تغطية الجرد %")
    inventory_adjust_value = fields.Float("قيمة فروقات الجرد")
    simulated = fields.Boolean("بيانات المحاكي", index=True)

    _company_date_uniq = models.Constraint("UNIQUE(company_id, date)", "يوجد سجل لهذا اليوم.")

    # ------------------------------------------------------------------ helpers
    @api.model
    def _tz(self, company):
        profile = self.env["resta.growth.profile"]._get(company)
        return pytz.timezone(profile.tz or "Asia/Riyadh")

    @api.model
    def _utc_bounds(self, tz, date_from, date_to):
        start = tz.localize(datetime.combine(date_from, time.min)).astimezone(pytz.utc).replace(tzinfo=None)
        end = tz.localize(datetime.combine(date_to + timedelta(days=1), time.min)).astimezone(pytz.utc).replace(tzinfo=None)
        return start, end

    @api.model
    def _local_day(self, tz, dt):
        return pytz.utc.localize(dt).astimezone(tz).date()

    # --------------------------------------------------------------- aggregation
    @api.model
    def _compute_range(self, company, date_from, date_to):
        """(Re)build the rows of [date_from, date_to] for a company. Simulated rows are kept."""
        profile = self.env["resta.growth.profile"]._get(company)
        tz = self._tz(company)
        start, end = self._utc_bounds(tz, date_from, date_to)
        days = defaultdict(lambda: defaultdict(float))
        cash_cond = self.env["resta.growth.condition"].sudo().search([("compute_key", "=", "cash_accuracy")], limit=1)
        min_abs = cash_cond.param_1 if cash_cond else 10.0
        pct = cash_cond.param_2 if cash_cond else 0.5

        self._agg_pos(company, tz, start, end, days, min_abs, pct)
        self._agg_invoices(company, tz, start, end, days)
        self._agg_stock(company, tz, start, end, days)

        existing = {r.date: r for r in self.sudo().search([
            ("company_id", "=", company.id), ("date", ">=", date_from), ("date", "<=", date_to)])}
        day = date_from
        to_create = []
        while day <= date_to:
            vals = days.get(day, {})
            row = {
                "planned": profile._is_planned_day(day),
                "recorded": bool(vals.get("recorded")),
                "orders": int(vals.get("orders", 0)),
                "sales_total": vals.get("sales_total", 0.0),
                "sales_untaxed": vals.get("sales_untaxed", 0.0),
                "cash_sales": vals.get("cash_sales", 0.0),
                "refunds_total": vals.get("refunds_total", 0.0),
                "backdated_orders": int(vals.get("backdated_orders", 0)),
                "sessions_closed": int(vals.get("sessions_closed", 0)),
                "sessions_late": int(vals.get("sessions_late", 0)),
                "sessions_cash_checked": int(vals.get("sessions_cash_checked", 0)),
                "sessions_cash_ok": int(vals.get("sessions_cash_ok", 0)),
                "cash_diff_abs": vals.get("cash_diff_abs", 0.0),
                "invoices_total": int(vals.get("invoices_total", 0)),
                "invoices_sent": int(vals.get("invoices_sent", 0)),
                "invoices_pending_48h": int(vals.get("invoices_pending_48h", 0)),
                "consumption_value": vals.get("consumption_value", 0.0),
                "waste_value": vals.get("waste_value", 0.0),
                "inventory_count": bool(vals.get("inventory_count")),
                "inventory_coverage": vals.get("inventory_coverage", 0.0),
                "inventory_adjust_value": vals.get("inventory_adjust_value", 0.0),
            }
            rec = existing.get(day)
            if rec:
                if not rec.simulated:
                    rec.sudo().write(row)
            else:
                to_create.append(dict(row, company_id=company.id, date=day))
            day += timedelta(days=1)
        if to_create:
            self.sudo().create(to_create)

    def _agg_pos(self, company, tz, start, end, days, min_abs, pct):
        if "pos.order" not in self.env:
            return
        cr = self.env.cr
        tzname = tz.zone
        cr.execute("""
            SELECT (o.date_order AT TIME ZONE 'UTC' AT TIME ZONE %(tz)s)::date AS d,
                   count(*) FILTER (WHERE o.amount_total >= 0),
                   coalesce(sum(o.amount_total) FILTER (WHERE o.amount_total >= 0), 0),
                   coalesce(sum(o.amount_total - o.amount_tax) FILTER (WHERE o.amount_total >= 0), 0),
                   coalesce(sum(-o.amount_total) FILTER (WHERE o.amount_total < 0), 0),
                   count(*) FILTER (WHERE o.create_date - o.date_order > interval '24 hours'),
                   bool_or(s.state = 'closed')
              FROM pos_order o
              JOIN pos_session s ON s.id = o.session_id
             WHERE o.company_id = %(c)s AND o.state IN %(st)s
               AND o.date_order >= %(start)s AND o.date_order < %(end)s
          GROUP BY 1
        """, {"tz": tzname, "c": company.id, "st": VALID_ORDER_STATES, "start": start, "end": end})
        for d, orders, total, untaxed, refunds, backdated, closed in cr.fetchall():
            v = days[d]
            v["orders"] += orders
            v["sales_total"] += float(total)
            v["sales_untaxed"] += float(untaxed)
            v["refunds_total"] += float(refunds)
            v["backdated_orders"] += backdated
            v["recorded"] = 1.0 if (closed and orders) else v.get("recorded", 0.0)

        cr.execute("""
            SELECT (o.date_order AT TIME ZONE 'UTC' AT TIME ZONE %(tz)s)::date, coalesce(sum(p.amount), 0)
              FROM pos_payment p
              JOIN pos_order o ON o.id = p.pos_order_id
              JOIN pos_payment_method m ON m.id = p.payment_method_id
             WHERE o.company_id = %(c)s AND o.state IN %(st)s AND m.is_cash_count
               AND o.date_order >= %(start)s AND o.date_order < %(end)s
          GROUP BY 1
        """, {"tz": tzname, "c": company.id, "st": VALID_ORDER_STATES, "start": start, "end": end})
        for d, cash in cr.fetchall():
            days[d]["cash_sales"] += float(cash)

        # sessions, by the local day they were closed
        cr.execute("""
            WITH sess AS (
                SELECT s.id, s.stop_at, s.start_at, s.config_id,
                       coalesce(s.cash_register_balance_start, 0) AS bal_start,
                       coalesce(s.cash_register_balance_end_real, 0) AS bal_real,
                       coalesce(s.cash_real_transaction, 0) AS cash_moves,
                       (SELECT coalesce(sum(p.amount), 0) FROM pos_payment p
                          JOIN pos_payment_method m ON m.id = p.payment_method_id
                         WHERE p.session_id = s.id AND m.is_cash_count) AS cash
                  FROM pos_session s
                  JOIN pos_config c ON c.id = s.config_id
                 WHERE c.company_id = %(c)s AND s.state = 'closed'
                   AND s.stop_at >= %(start)s AND s.stop_at < %(end)s
            )
            SELECT (stop_at AT TIME ZONE 'UTC' AT TIME ZONE %(tz)s)::date,
                   stop_at - start_at > interval '26 hours',
                   bal_real - (bal_start + cash_moves + cash),
                   cash,
                   EXISTS (SELECT 1 FROM pos_config_pos_payment_method_rel r
                             JOIN pos_payment_method m ON m.id = r.pos_payment_method_id
                            WHERE r.pos_config_id = sess.config_id AND m.is_cash_count)
              FROM sess
        """, {"tz": tzname, "c": company.id, "start": start, "end": end})
        for d, late, diff, cash, has_cash in cr.fetchall():
            v = days[d]
            v["sessions_closed"] += 1
            v["sessions_late"] += 1 if late else 0
            if has_cash:
                v["sessions_cash_checked"] += 1
                v["cash_diff_abs"] += abs(float(diff))
                if abs(float(diff)) <= max(min_abs, float(cash) * pct / 100.0):
                    v["sessions_cash_ok"] += 1

    def _agg_invoices(self, company, tz, start, end, days):
        Move = self.env["account.move"].sudo()
        has_edi = "edi_state" in Move._fields
        moves = Move.search_fetch([
            ("company_id", "=", company.id), ("state", "=", "posted"),
            ("move_type", "in", ("out_invoice", "out_refund")),
            ("create_date", ">=", start), ("create_date", "<", end),
        ], ["create_date"] + (["edi_state"] if has_edi else []))
        limit = fields.Datetime.now() - timedelta(hours=48)
        for m in moves:
            v = days[self._local_day(tz, m.create_date)]
            v["invoices_total"] += 1
            state = m.edi_state if has_edi else False
            if state == "sent":
                v["invoices_sent"] += 1
            elif state == "to_send" and m.create_date < limit:
                v["invoices_pending_48h"] += 1

    def _agg_stock(self, company, tz, start, end, days):
        Move = self.env["stock.move"].sudo().with_context(active_test=False)
        base = [("company_id", "=", company.id), ("state", "=", "done"),
                ("date", ">=", start), ("date", "<", end)]
        fetch = ["date", "product_id", "product_qty", "value", "location_dest_usage"]
        counted = defaultdict(set)
        for m in Move.search_fetch(base + ["|", "|", ("scrap_id", "!=", False), ("is_inventory", "=", True),
                                           ("location_dest_usage", "in", ("customer", "production"))],
                                   fetch + ["scrap_id", "is_inventory"]):
            value = abs(m.value) or m.product_qty * m.product_id.standard_price
            v = days[self._local_day(tz, m.date)]
            if m.scrap_id:
                v["waste_value"] += value
            elif m.is_inventory:
                v["inventory_count"] = 1.0
                v["inventory_adjust_value"] += value
                counted[self._local_day(tz, m.date)].add(m.product_id.id)
            else:
                v["consumption_value"] += value
        if counted:
            on_hand = self._on_hand_values(company)
            total = sum(on_hand.values())
            for d, product_ids in counted.items():
                part = sum(on_hand.get(pid, 0.0) for pid in product_ids)
                days[d]["inventory_coverage"] = round(100.0 * part / total, 1) if total else 0.0

    @api.model
    def _on_hand_values(self, company):
        """Current value of the stock on hand per product (internal locations)."""
        groups = self.env["stock.quant"].sudo()._read_group(
            [("company_id", "=", company.id), ("location_id.usage", "=", "internal"), ("quantity", ">", 0)],
            ["product_id"], ["quantity:sum"])
        return {p.id: qty * p.with_company(company).standard_price for p, qty in groups}

    # --------------------------------------------------------------------- cron
    @api.model
    def _cron_aggregate(self, days_back=7):
        today = fields.Date.context_today(self)
        for company in self.env["res.company"].sudo().search([]):
            self._compute_range(company, today - timedelta(days=days_back), today)

    @api.model
    def _backfill(self, company, days=120):
        today = fields.Date.context_today(self)
        self._compute_range(company, today - timedelta(days=days), today)
