# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import api, fields, models


class PosOrder(models.Model):
    _inherit = 'pos.order'

    business_date = fields.Date(
        string='Business Date',
        compute='_compute_business_date',
        store=True,
        index=True,
        help="The operational business day this order belongs to, based on "
             "the company's 'Business Day Start Hour' setting. Orders placed "
             "after midnight but before that hour are attributed to the "
             "previous business day instead of the calendar date.\n"
             "يوم العمل التشغيلي لهذا الطلب حسب إعداد 'ساعة بداية اليوم "
             "التشغيلي'. الطلبات بعد منتصف الليل وقبل هذه الساعة تُنسب "
             "ليوم العمل السابق بدل التاريخ التقويمي.",
    )

    @api.depends('date_order', 'company_id')
    def _compute_business_date(self):
        for order in self:
            order.business_date = order._get_business_date(order.date_order)

    def _get_business_date(self, dt):
        """Return the business (operational) date for a given UTC datetime,
        based on this record's company timezone and business_day_start_hour.
        """
        if not dt:
            return False
        company = self.company_id or self.env.company
        start_hour = company.business_day_start_hour or 0.0

        tz_name = company.partner_id.tz or self.env.context.get('tz') or 'UTC'
        local_dt = fields.Datetime.context_timestamp(
            self.with_context(tz=tz_name), dt
        )
        hour_float = local_dt.hour + (local_dt.minute / 60.0)

        business_dt = local_dt
        if hour_float < start_hour:
            business_dt = local_dt - timedelta(days=1)
        return business_dt.date()
