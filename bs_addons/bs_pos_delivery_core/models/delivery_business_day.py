"""Connect ↔ pos_business_day: operational business day for delivery orders.

A delivery order belongs to the *business day* of its company, exactly like a
POS order handled by ``pos_business_day``: orders received after midnight but
before ``res.company.business_day_start_hour`` count for the previous day.
The timezone rule mirrors ``pos.order._get_business_date`` so POS sales and
Connect sales of the same night land on the same business date.
"""

from datetime import datetime, time, timedelta

import pytz

from odoo import api, fields, models

_RECOMPUTE_BATCH = 1000


class ResCompany(models.Model):
    _inherit = "res.company"

    def _bs_business_tz(self):
        self.ensure_one()
        return pytz.timezone(
            self.partner_id.tz or self.env.context.get("tz") or self.env.user.tz or "UTC"
        )

    def _bs_business_start_hour(self):
        self.ensure_one()
        return self.sudo().business_day_start_hour or 0.0

    def _bs_business_date(self, value):
        """Business date of a naive-UTC datetime for this company."""
        self.ensure_one()
        if not value:
            return False
        value = fields.Datetime.to_datetime(value)
        local = pytz.UTC.localize(value).astimezone(self._bs_business_tz())
        if local.hour + local.minute / 60.0 < self._bs_business_start_hour():
            local -= timedelta(days=1)
        return local.date()

    def _bs_current_business_date(self):
        self.ensure_one()
        return self._bs_business_date(fields.Datetime.now())

    def _bs_business_day_bounds(self, date_from, date_to=None):
        """Naive-UTC ``[start, end)`` covering business days date_from..date_to."""
        self.ensure_one()
        date_from = fields.Date.to_date(date_from)
        date_to = fields.Date.to_date(date_to) or date_from
        timezone = self._bs_business_tz()
        start_hour = self._bs_business_start_hour()
        offset = timedelta(hours=start_hour)

        def _utc(day):
            local = timezone.localize(datetime.combine(day, time.min) + offset)
            return local.astimezone(pytz.UTC).replace(tzinfo=None)

        return _utc(date_from), _utc(date_to + timedelta(days=1))

    def _bs_recompute_delivery_business_dates(self):
        """Re-attribute existing Connect orders after a start-hour change."""
        orders_model = self.env["bs.delivery.order"].sudo().with_context(active_test=False)
        last_id = 0
        while True:
            orders = orders_model.search(
                [("company_id", "in", self.ids), ("id", ">", last_id)],
                order="id",
                limit=_RECOMPUTE_BATCH,
            )
            if not orders:
                break
            self.env.add_to_compute(orders_model._fields["business_date"], orders)
            orders.flush_recordset(["business_date"])
            last_id = orders[-1].id
            orders.invalidate_recordset(["business_date"])
        return True

    def write(self, vals):
        result = super().write(vals)
        if "business_day_start_hour" in vals:
            self._bs_recompute_delivery_business_dates()
        return result


class PartnerTimezone(models.Model):
    _inherit = "res.partner"

    def write(self, vals):
        result = super().write(vals)
        if "tz" in vals:
            companies = (
                self.env["res.company"].sudo().search([("partner_id", "in", self.ids)])
            )
            if companies:
                companies._bs_recompute_delivery_business_dates()
        return result


class BusinessDayRecomputeJob(models.Model):
    _inherit = "pos.business.day.recompute.job"

    def _mark_done(self):
        # The pos_business_day "recompute" action also re-attributes Connect
        # orders, so POS and delivery reports always share the same day split.
        result = super()._mark_done()
        self.company_id._bs_recompute_delivery_business_dates()
        return result


class DeliveryOrderBusinessDay(models.Model):
    _inherit = "bs.delivery.order"

    business_date = fields.Date(
        string="Business Date",
        compute="_compute_business_date",
        store=True,
        index=True,
        copy=False,
        help="Operational business day of the order (pos_business_day). Orders "
        "received after midnight but before the company's business day start "
        "hour belong to the previous business day.",
    )

    @api.depends("received_at", "company_id")
    def _compute_business_date(self):
        for order in self:
            company = order.company_id or self.env.company
            order.business_date = company._bs_business_date(order.received_at)
