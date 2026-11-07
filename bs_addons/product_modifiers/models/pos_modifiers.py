# -*- coding: utf-8 -*-
# Powered by Kanak Infosystems LLP.
# © 2020 Kanak Infosystems LLP. (<https://www.kanakinfosystems.com>).

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class POSmodifiers(models.Model):
    _name = 'pos.modifiers'
    _inherit = ['pos.load.mixin']
    _description = 'POS Modifiers'

    name = fields.Many2one('product.product', string='Name', required=True)
    desc = fields.Char(string='Description')
    price = fields.Float(string='Price')
    pos_categoryid = fields.Many2one('pos.category')


class Productmodifiers(models.Model):
    _name = 'product.modifiers'
    _inherit = ['pos.load.mixin']
    _description = 'Product Modifiers'

    name = fields.Many2one('product.product', string='Name', required=True)
    desc = fields.Char(string='Description')
    price = fields.Float(string='Price')
    product_id = fields.Many2one('product.template')
    apply_selector = fields.Boolean('Apply Selector')
    min_selector = fields.Integer('Min Selector', default=0)
    max_selector = fields.Integer('Max Selector', default=3)

    @api.constrains('apply_selector', 'min_selector', 'max_selector')
    def _check_selector_limits(self):
        for modifier in self.filtered('apply_selector'):
            if modifier.min_selector < 0 or modifier.max_selector < 0:
                raise ValidationError('Selector limits cannot be negative.')
            if modifier.min_selector > modifier.max_selector:
                raise ValidationError('Minimum selector cannot exceed maximum selector.')

    @api.model
    def _load_pos_data_fields(self, config):
        return [
            'id', 'name', 'desc', 'price', 'product_id',
            'apply_selector', 'min_selector', 'max_selector',
        ]


class ModifiersGroups(models.Model):
    _name = 'modifiers.groups'
    _inherit = ['pos.load.mixin', 'mail.thread', 'mail.activity.mixin']
    _description = 'Modifiers Groups'

    name = fields.Char(string='Toppings', required=True, tracking=True)
    modifier_ids = fields.Many2many(
        'product.template', 'group_id', 'tmpl_id', string="Modifiers",
        domain=[('use_as_modifier', '=', True)], tracking=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        groups = super().create(vals_list)
        groups._set_products_as_modifiers()
        if (
            not self.env.context.get('tracking_disable')
            and not self.env.context.get('mail_create_nolog')
        ):
            for group in groups:
                group.message_post(
                    body=_("Modifiers group created."),
                    subtype_xmlid='mail.mt_note',
                )
        return groups

    def write(self, vals):
        tracked_fields = ('name', 'modifier_ids')
        fields_to_track = [field for field in tracked_fields if field in vals]
        tracking_disabled = (
            self.env.context.get('tracking_disable')
            or self.env.context.get('mail_notrack')
            or not fields_to_track
        )
        initial_values_by_id = {}
        records_to_write = self
        if not tracking_disabled:
            initial_values_by_id = {
                record.id: {field: record[field] for field in fields_to_track}
                for record in self
            }
            records_to_write = self.with_context(mail_notrack=True)

        result = super(ModifiersGroups, records_to_write).write(vals)
        if 'modifier_ids' in vals:
            self._set_products_as_modifiers()

        if not tracking_disabled:
            tracked_fields_info = self.fields_get(
                fields_to_track,
                attributes=('string', 'type', 'selection', 'currency_field'),
            )
            for record in self:
                changes, tracking_value_ids = record._mail_track(
                    tracked_fields_info,
                    initial_values_by_id[record.id],
                )
                if changes and tracking_value_ids:
                    record._message_log(
                        body=_("Modifiers group changed."),
                        tracking_value_ids=tracking_value_ids,
                    )
        return result

    def _set_products_as_modifiers(self):
        self.mapped('modifier_ids').filtered(
            lambda product: not product.use_as_modifier
        ).write({'use_as_modifier': True})
