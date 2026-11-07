# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError


class ResCompany(models.Model):
    _inherit = 'res.company'

    business_day_start_hour = fields.Float(
        string='Business Day Start Hour',
        default=6.0,
        help="The hour of the day (0-24) at which a new 'business day' "
             "starts for this company. Example: if your shift runs from "
             "6:00 AM to 2:00 AM the next calendar day, set this to 6.0. "
             "Any POS order or stock move that happens between midnight "
             "and this hour will be attributed to the previous business "
             "day in reports.\n"
             "ساعة بداية يوم العمل التشغيلي. مثال: دوام من 6 صباحاً حتى "
             "2 صباحاً اليوم التالي => ضع القيمة 6.0. أي عملية بيع أو "
             "حركة مخزون تحدث بين منتصف الليل وهذه الساعة تُنسب إلى يوم "
             "العمل السابق في التقارير.",
    )

    @api.constrains('business_day_start_hour')
    def _check_business_day_start_hour(self):
        for company in self:
            if not 0 <= company.business_day_start_hour < 24:
                raise ValidationError(_(
                    "Business Day Start Hour must be between 0 and 24, "
                    "excluding 24."
                ))

    def _create_business_day_recompute_job(self, **values):
        self.ensure_one()
        vals = {
            'company_id': self.id,
            'requested_by_id': values.pop('requested_by_id', self.env.user.id),
            'recompute_scope': values.pop('recompute_scope', 'all'),
            'batch_size': values.pop('batch_size', 500),
        }
        vals.update(values)
        return self.env['pos.business.day.recompute.job'].sudo().create(vals)

    def _recompute_business_dates(self):
        return self._create_business_day_recompute_job()

    def action_recompute_business_dates(self):
        """Queue recompute of business_date on existing records in batches."""
        self.ensure_one()
        if not (
            self.env.user.has_group('point_of_sale.group_pos_manager')
            or self.env.user.has_group('base.group_system')
        ):
            raise AccessError(_(
                "Only Point of Sale managers can recompute business dates."
            ))
        if self.id not in self.env.user.company_ids.ids:
            raise AccessError(_(
                "You can only recompute business dates for your allowed "
                "companies."
            ))

        job = self.sudo()._create_business_day_recompute_job(
            requested_by_id=self.env.user.id,
        )
        return {
            'type': 'ir.actions.act_window',
            'name': _('Business Day Recompute Job'),
            'res_model': 'pos.business.day.recompute.job',
            'res_id': job.id,
            'view_mode': 'form',
            'target': 'current',
        }
