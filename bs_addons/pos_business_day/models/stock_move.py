# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import api, fields, models


class StockMove(models.Model):
    _inherit = 'stock.move'

    business_date = fields.Date(
        string='Business Date',
        compute='_compute_business_date',
        store=True,
        index=True,
        help="The operational business/inventory day this stock move "
             "belongs to, based on the company's 'Business Day Start Hour' "
             "setting.\n"
             "اليوم المخزني (التشغيلي) لهذه الحركة حسب إعداد 'ساعة بداية "
             "اليوم التشغيلي' على مستوى الشركة.",
    )

    @api.depends('date', 'company_id')
    def _compute_business_date(self):
        for move in self:
            move.business_date = move._get_business_date(move.date)

    def _get_business_date(self, dt):
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
