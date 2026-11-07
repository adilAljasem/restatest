from collections import OrderedDict
from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .labels import get_labels, lang_code, resolve_lang_mode

VALID_STATES = ("paid", "done")
MAX_LISTED_ORDERS = 40


class PosClosingReportData(models.AbstractModel):
    """Builds the data of the two closing reports (POS receipt and PDF).

    * Cashier report: one session (``pos.closing.report.get_receipt_data``).
    * Branch manager report: every session of a business day, the day being
      defined by the 'Business Day Start Hour' of pos_business_day.
    """

    _name = "pos.closing.report.data"
    _description = "POS Closing Report Data"

    # ------------------------------------------------------------------
    # Common helpers
    # ------------------------------------------------------------------
    @api.model
    def _lang_mode(self, config, lang_mode=None):
        mode = lang_mode or (config and config.pcr_language) or "user"
        return resolve_lang_mode(mode, self.env.lang)

    @api.model
    def _style(self, config):
        """Print settings taken from the kitchen ticket module."""
        use_kitchen = bool(config) and config.pcr_use_kitchen_style
        width = config.kt_print_width if use_kitchen else "512"
        return {
            "kitchen": use_kitchen,
            "fontSize": (config.kt_font_size if use_kitchen else "") or "normal",
            "printWidth": width if width in ("576", "512", "420") else "576",
        }

    @api.model
    def _header(self, config, mode):
        labels, labels2 = get_labels(mode)
        return {
            "lang": mode,
            "direction": "rtl" if mode in ("ar", "both") else "ltr",
            "labels": labels,
            "labels2": labels2,
            "style": self._style(config),
            "printed_at": self._fmt_dt(fields.Datetime.now()),
            "printed_by": self.env.user.name,
        }

    @api.model
    def _tz(self, company=None):
        company = company or self.env.company
        return company.partner_id.tz or self.env.context.get("tz") or self.env.user.tz or "UTC"

    @api.model
    def _fmt_dt(self, dt, tz_name=None):
        if not dt:
            return ""
        tz = pytz.timezone(tz_name or self.env.context.get("tz") or self.env.user.tz or "UTC")
        return pytz.utc.localize(fields.Datetime.to_datetime(dt)).astimezone(tz).strftime("%Y-%m-%d %H:%M")

    @api.model
    def _names(self, records, mode):
        """{id: (name, second name)} of products / payment methods for the report language."""
        records = records.exists()
        if not records:
            return {}
        installed = dict(self.env["res.lang"].get_installed())
        ctx = {"display_default_code": False}

        def read(code):
            # en_US is the source language: always readable, even when not activated
            if code != "en_US" and code not in installed:
                code = self.env.lang or "en_US"
            return {r.id: r.display_name for r in records.with_context(lang=code, **ctx)}

        main = read(lang_code(mode))
        second = read("en_US") if mode == "both" else {}
        return {
            rid: (name, second.get(rid) if second.get(rid) and second.get(rid) != name else "")
            for rid, name in main.items()
        }

    # ------------------------------------------------------------------
    # Orders: summary, sold items, refunds, cancellations
    # ------------------------------------------------------------------
    @api.model
    def _group_lines(self, lines, mode, sign=1):
        """Sold (or refunded) items grouped by product, largest quantity first."""
        lines = lines.filtered(lambda l: not l.combo_parent_id)
        names = self._names(lines.product_id, mode)
        grouped = OrderedDict()
        for line in lines:
            entry = grouped.setdefault(line.product_id.id, {"qty": 0.0, "amount": 0.0})
            entry["qty"] += line.qty * sign
            entry["amount"] += (line.price_subtotal_incl + sum(line.combo_line_ids.mapped("price_subtotal_incl"))) * sign
        items = []
        for product_id, entry in grouped.items():
            name, name2 = names.get(product_id, ("", ""))
            items.append({
                "name": name,
                "name2": name2,
                "qty": round(entry["qty"], 3),
                "amount": entry["amount"],
            })
        items.sort(key=lambda i: (-i["qty"], i["name"]))
        return items

    @api.model
    def _order_ref(self, order):
        return order.tracking_number or order.pos_reference or order.name

    @api.model
    def _orders_data(self, orders, currency, mode, show_items, show_refunds, show_cancellations):
        orders = orders.sudo()
        valid = orders.filtered(lambda o: o.state in VALID_STATES)
        refunds = valid.filtered(lambda o: o.is_refund or o.amount_total < 0)
        sales = valid - refunds
        cancelled = orders.filtered(lambda o: o.state == "cancel" and o.lines)

        gross = sum(sales.mapped("amount_total"))
        refund_amount = sum(refunds.mapped("amount_total"))
        guests = sum(sales.mapped("customer_count")) if "customer_count" in orders._fields else 0
        data = {
            "summary": {
                "orders_count": len(sales),
                "gross_sales": currency.round(gross),
                "refunds_count": len(refunds),
                "refunds_total": currency.round(refund_amount),
                "net_sales": currency.round(gross + refund_amount),
                "taxes": currency.round(sum(valid.mapped("amount_tax"))),
                "average_ticket": currency.round(gross / len(sales)) if sales else 0.0,
                "guests": guests,
                "cancelled_count": len(cancelled),
                "cancelled_total": currency.round(sum(cancelled.mapped("amount_total"))),
            },
            "items": [],
            "items_qty": 0.0,
            "items_total": 0.0,
            "refund_items": [],
            "refund_orders": [],
            "cancelled_orders": [],
            "cancelled_more": 0,
            "refund_more": 0,
        }
        if show_items:
            items = self._group_lines(sales.lines, mode)
            for item in items:
                item["amount"] = currency.round(item["amount"])
            data["items"] = items
            data["items_qty"] = round(sum(i["qty"] for i in items), 3)
            data["items_total"] = currency.round(sum(i["amount"] for i in items))
        if show_refunds:
            items = self._group_lines(refunds.lines, mode, sign=-1)
            for item in items:
                item["amount"] = currency.round(item["amount"])
            data["refund_items"] = items
            listed = refunds.sorted("date_order")
            data["refund_orders"] = [{
                "ref": self._order_ref(o),
                "origin": self._order_ref(o.refunded_order_id) if o.refunded_order_id else "",
                "time": self._fmt_dt(o.date_order)[-5:],
                "employee": self._order_employee(o),
                "amount": currency.round(-o.amount_total),
            } for o in listed[:MAX_LISTED_ORDERS]]
            data["refund_more"] = max(len(listed) - MAX_LISTED_ORDERS, 0)
        if show_cancellations:
            listed = cancelled.sorted("date_order")
            data["cancelled_orders"] = [{
                "ref": self._order_ref(o),
                "time": self._fmt_dt(o.date_order)[-5:],
                "employee": self._order_employee(o),
                "amount": currency.round(o.amount_total),
            } for o in listed[:MAX_LISTED_ORDERS]]
            data["cancelled_more"] = max(len(listed) - MAX_LISTED_ORDERS, 0)
        return data

    @api.model
    def _order_employee(self, order):
        employee = order.employee_id if "employee_id" in order._fields else False
        return (employee and employee.name) or order.user_id.name or ""

    @api.model
    def _payments_data(self, orders, currency, mode):
        """Payments of the orders grouped by payment method."""
        payments = self.env["pos.payment"].sudo().search([
            ("pos_order_id", "in", orders.filtered(lambda o: o.state in VALID_STATES).ids),
        ])
        methods = payments.payment_method_id
        names = self._names(methods, mode)
        rows = []
        for method in methods.sorted(lambda m: (not m.is_cash_count, m.sequence if "sequence" in m._fields else 0, m.id)):
            method_payments = payments.filtered(lambda p: p.payment_method_id == method)
            name, name2 = names.get(method.id, (method.name, ""))
            rows.append({
                "name": name,
                "name2": name2,
                "type": "cash" if method.is_cash_count else (method.type or "bank"),
                "count": len(method_payments.pos_order_id),
                "amount": currency.round(sum(method_payments.mapped("amount"))),
            })
        return rows

    # ------------------------------------------------------------------
    # Cashier report (one session)
    # ------------------------------------------------------------------
    @api.model
    def cashier_report_data(self, report, lang_mode=None):
        report = report.sudo()
        session = report.session_id
        config = report.config_id or session.config_id
        mode = self._lang_mode(config, lang_mode)
        labels = get_labels(mode)[0]
        cur = report.currency_id or session.currency_id or self.env.company.currency_id
        method_names = self._names(report.line_ids.payment_method_id, mode)
        data = self._header(config, mode)
        data.update({
            "kind": "cashier",
            "company": report.company_id.name,
            "branch": report.branch_name,
            "cashier": report.cashier_name,
            "session": report.name,
            "start_at": self._fmt_dt(report.start_at),
            "stop_at": self._fmt_dt(report.stop_at),
            "lines": [{
                "name": method_names.get(line.payment_method_id.id, (line.name, ""))[0] or line.name,
                "name2": method_names.get(line.payment_method_id.id, ("", ""))[1],
                "type": line.method_type,
                "type_label": labels.get(line.method_type, ""),
                "opening": cur.round(line.opening),
                "expected": cur.round(line.expected),
                "counted": cur.round(line.counted),
                "difference": cur.round(line.difference),
                "diff_known": line.diff_known,
            } for line in report.line_ids],
            "expected_total": cur.round(report.expected_total),
            "counted_total": cur.round(report.counted_total),
            "difference_total": cur.round(report.difference_total),
            "status": report.status,
            "status_label": labels.get(report.status, ""),
            "has_retro": any(not line.diff_known for line in report.line_ids),
            "show": {
                "summary": config.pcr_cashier_show_summary,
                "items": config.pcr_cashier_show_items,
                "refunds": config.pcr_cashier_show_refunds,
                "cancellations": config.pcr_cashier_show_cancellations,
                "sessions": False,
                "payments": False,
            },
        })
        data.update(self._orders_data(
            session.order_ids, cur, mode,
            show_items=config.pcr_cashier_show_items,
            show_refunds=config.pcr_cashier_show_refunds,
            show_cancellations=config.pcr_cashier_show_cancellations,
        ))
        return data

    # ------------------------------------------------------------------
    # Branch manager report (business day)
    # ------------------------------------------------------------------
    @api.model
    def _current_business_date(self, company):
        """Business day running now (after midnight and before the start hour: the previous day)."""
        return self.env["pos.order"].with_company(company)._get_business_date(fields.Datetime.now())

    @api.model
    def _business_day_window(self, company, business_date):
        """(start, stop) in UTC of a business day: from the start hour to the start hour of the next day."""
        hours = company.business_day_start_hour or 0.0
        hour = int(hours)
        minute = int(round((hours - hour) * 60))
        if minute == 60:
            hour, minute = hour + 1, 0
        tz = pytz.timezone(self._tz(company))
        local_start = tz.localize(datetime.combine(business_date, time(hour % 24, minute)))
        local_stop = tz.localize(datetime.combine(business_date + timedelta(days=1), time(hour % 24, minute)))
        to_utc = lambda dt: dt.astimezone(pytz.utc).replace(tzinfo=None)
        return to_utc(local_start), to_utc(local_stop)

    @api.model
    def _day_configs(self, config):
        if config.pcr_day_scope == "company":
            return self.env["pos.config"].sudo().with_context(active_test=False).search([
                ("company_id", "=", config.company_id.id),
            ])
        return config

    @api.model
    def _session_row(self, session, mode):
        labels = get_labels(mode)[0]
        cur = session.currency_id
        report = self.env["pos.closing.report"].sudo().search([("session_id", "=", session.id)], limit=1)
        if report:
            lines = [{
                "type": l.method_type, "expected": l.expected, "counted": l.counted, "known": True,
            } for l in report.line_ids]
            cashier = report.cashier_name
            stop_at = report.stop_at
        else:
            lines = [{
                "type": l["method_type"], "expected": l["expected"], "counted": l["expected"], "known": False,
            } for l in self.env["pos.closing.report"]._prepare_lines(session)]
            cashier = session._closing_cashier_guess()
            stop_at = session.stop_at
        counted_known = bool(report)

        def total(kind, key):
            return cur.round(sum(l[key] for l in lines if l["type"] == kind))

        cash_expected, cash_counted = total("cash", "expected"), total("cash", "counted")
        bank_expected, bank_counted = total("bank", "expected"), total("bank", "counted")
        diff = cur.round((cash_counted + bank_counted) - (cash_expected + bank_expected)) if counted_known else 0.0
        if not counted_known:
            status = session.state if session.state in ("opened", "closing_control") else "closed"
            status = {"opened": "open", "closing_control": "closing"}.get(status, status)
        elif cur.is_zero(diff):
            status = "balanced"
        else:
            status = "shortage" if diff < 0 else "surplus"
        return {
            "name": session.name,
            "branch": session.config_id.name,
            "cashier": cashier,
            "start_at": self._fmt_dt(session.start_at),
            "stop_at": self._fmt_dt(stop_at),
            "counted_known": counted_known,
            "cash_expected": cash_expected,
            "cash_counted": cash_counted,
            "cash_difference": cur.round(cash_counted - cash_expected) if counted_known else 0.0,
            "bank_expected": bank_expected,
            "bank_counted": bank_counted,
            "bank_difference": cur.round(bank_counted - bank_expected) if counted_known else 0.0,
            "expected_total": cur.round(cash_expected + bank_expected),
            "counted_total": cur.round(cash_counted + bank_counted) if counted_known else 0.0,
            "difference_total": diff,
            "status": status,
            "status_label": labels.get(status, ""),
        }

    @api.model
    def business_day_data(self, config, business_date=None, lang_mode=None):
        config = config.sudo()
        company = config.company_id
        if "business_day_start_hour" not in company._fields:
            raise UserError(_("The Business Day module (pos_business_day) is required for this report."))
        business_date = fields.Date.to_date(business_date) if business_date else self._current_business_date(company)
        start, stop = self._business_day_window(company, business_date)
        configs = self._day_configs(config)
        mode = self._lang_mode(config, lang_mode)
        cur = config.currency_id or company.currency_id

        orders = self.env["pos.order"].sudo().search([
            ("config_id", "in", configs.ids),
            ("business_date", "=", business_date),
        ])
        sessions = orders.session_id | self.env["pos.session"].sudo().search([
            ("config_id", "in", configs.ids),
            ("start_at", "<", fields.Datetime.to_string(stop)),
            "|", ("stop_at", "=", False), ("stop_at", ">=", fields.Datetime.to_string(start)),
        ])
        sessions = sessions.sorted(lambda s: (s.start_at or datetime.max, s.id))
        rows = [self._session_row(s, mode) for s in sessions]

        tz_name = self._tz(company)
        data = self._header(config, mode)
        data.update({
            "kind": "day",
            "company": company.name,
            "branch": ", ".join(configs.mapped("name")),
            "business_date": fields.Date.to_string(business_date),
            "period_from": self._fmt_dt(start, tz_name),
            "period_to": self._fmt_dt(stop, tz_name),
            "sessions_count": len(sessions),
            "sessions": rows,
            "payments": self._payments_data(orders, cur, mode),
            "lines": [],
            "cash_expected": cur.round(sum(r["cash_expected"] for r in rows)),
            "cash_counted": cur.round(sum(r["cash_counted"] for r in rows if r["counted_known"])),
            "cash_difference": cur.round(sum(r["cash_difference"] for r in rows)),
            "bank_difference": cur.round(sum(r["bank_difference"] for r in rows)),
            "expected_total": cur.round(sum(r["expected_total"] for r in rows)),
            "counted_total": cur.round(sum(r["counted_total"] for r in rows)),
            "difference_total": cur.round(sum(r["difference_total"] for r in rows)),
            "has_open": any(not r["counted_known"] for r in rows),
            "show": {
                "summary": config.pcr_day_show_summary,
                "items": config.pcr_day_show_items,
                "refunds": config.pcr_day_show_refunds,
                "cancellations": config.pcr_day_show_cancellations,
                "sessions": config.pcr_day_show_sessions,
                "payments": True,
            },
        })
        data["payments_total"] = cur.round(sum(p["amount"] for p in data["payments"]))
        data.update(self._orders_data(
            orders, cur, mode,
            show_items=config.pcr_day_show_items,
            show_refunds=config.pcr_day_show_refunds,
            show_cancellations=config.pcr_day_show_cancellations,
        ))
        return data

    # ------------------------------------------------------------------
    # Access: branch manager report
    # ------------------------------------------------------------------
    @api.model
    def _check_day_report_access(self, config, employee_id=None):
        """Allowed people set on the POS (Closing Report settings); when none are set:
        POS managers (user) or employees set as managers of this POS (pos_hr)."""
        if self.env.su:
            return True
        user = self.env.user
        employee = False
        if employee_id and "hr.employee" in self.env:
            employee = self.env["hr.employee"].sudo().browse(int(employee_id)).exists()
        can_print = config.sudo()._pcr_day_can_print(user, employee)
        if can_print is None:
            if user.has_group("point_of_sale.group_pos_manager"):
                return True
            managers = config.sudo().advanced_employee_ids if "advanced_employee_ids" in config._fields else False
            can_print = bool(employee) and (
                (managers and employee in managers)
                or (employee.user_id and employee.user_id.has_group("point_of_sale.group_pos_manager"))
            )
        if can_print:
            return True
        raise AccessError(_("You are not allowed to print the business day report."))
