# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError


class PosBusinessDaySettings(models.TransientModel):
    _name = 'pos.business.day.settings'
    _description = 'POS Business Day Settings'

    company_id = fields.Many2one(
        'res.company',
        string='Company',
        required=True,
        default=lambda self: self.env.company,
    )
    business_day_start_hour = fields.Float(
        string='Business Day Start Hour',
        required=True,
        help="The hour of the day (0-24) at which a new business day starts "
             "for this company.",
    )
    recompute_scope = fields.Selection(
        [
            ('all', 'All Existing Records'),
            ('missing', 'Only Empty Business Dates'),
            ('date_range', 'Date Range'),
        ],
        string='Recompute Scope',
        default='all',
        required=True,
    )
    date_from = fields.Date(string='Date From')
    date_to = fields.Date(string='Date To')
    batch_size = fields.Integer(
        string='Batch Size',
        default=500,
        required=True,
        help="Maximum number of records processed in each cron run.",
    )

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        company = self.env.company
        if 'company_id' in fields_list:
            values.setdefault('company_id', company.id)
        if 'business_day_start_hour' in fields_list:
            values.setdefault(
                'business_day_start_hour',
                company.sudo().business_day_start_hour,
            )
        return values

    @api.onchange('company_id')
    def _onchange_company_id(self):
        if self.company_id:
            self.business_day_start_hour = (
                self.company_id.sudo().business_day_start_hour
            )

    @api.constrains('business_day_start_hour')
    def _check_business_day_start_hour(self):
        for wizard in self:
            if not 0 <= wizard.business_day_start_hour < 24:
                raise ValidationError(_(
                    "Business Day Start Hour must be between 0 and 24, "
                    "excluding 24."
                ))

    @api.constrains('batch_size', 'recompute_scope', 'date_from', 'date_to')
    def _check_recompute_values(self):
        for wizard in self:
            if wizard.batch_size < 1 or wizard.batch_size > 10000:
                raise ValidationError(_(
                    "Batch Size must be between 1 and 10000."
                ))
            if wizard.date_from and wizard.date_to and wizard.date_from > wizard.date_to:
                raise ValidationError(_(
                    "Date From must be before Date To."
                ))
            if wizard.recompute_scope == 'date_range' and not (
                wizard.date_from or wizard.date_to
            ):
                raise ValidationError(_(
                    "Set Date From or Date To when using Date Range."
                ))

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._apply_settings()
        return records

    def write(self, vals):
        result = super().write(vals)
        self._apply_settings()
        return result

    def _check_can_manage_business_day_settings(self):
        if not (
            self.env.user.has_group('point_of_sale.group_pos_manager')
            or self.env.user.has_group('base.group_system')
        ):
            raise AccessError(_(
                "Only Point of Sale managers can change business day settings."
            ))

        allowed_company_ids = set(self.env.user.company_ids.ids)
        for wizard in self:
            if wizard.company_id.id not in allowed_company_ids:
                raise AccessError(_(
                    "You can only change business day settings for your "
                    "allowed companies."
                ))

    def _apply_settings(self):
        self._check_can_manage_business_day_settings()
        for wizard in self:
            wizard.company_id.sudo().write({
                'business_day_start_hour': wizard.business_day_start_hour,
            })

    def action_apply(self):
        self.ensure_one()
        self._apply_settings()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'sticky': False,
                'message': _('Business day settings have been saved.'),
            },
        }

    def action_apply_and_recompute(self):
        self.ensure_one()
        self._apply_settings()
        job = self.env['pos.business.day.recompute.job'].sudo().create({
            'company_id': self.company_id.id,
            'requested_by_id': self.env.user.id,
            'recompute_scope': self.recompute_scope,
            'date_from': self.date_from,
            'date_to': self.date_to,
            'batch_size': self.batch_size,
        })
        return {
            'type': 'ir.actions.act_window',
            'name': _('Business Day Recompute Job'),
            'res_model': 'pos.business.day.recompute.job',
            'res_id': job.id,
            'view_mode': 'form',
            'target': 'current',
        }
