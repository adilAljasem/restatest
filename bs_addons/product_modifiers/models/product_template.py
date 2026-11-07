# -*- coding: utf-8 -*-
# Powered by Kanak Infosystems LLP.
# © 2020 Kanak Infosystems LLP. (<https://www.kanakinfosystems.com>).

from odoo import Command, api, fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    is_modifier = fields.Boolean('Is Modifier Product?')
    use_as_modifier = fields.Boolean('Modifier Only (hidden in POS grid)')
    modifier_ids = fields.One2many('product.modifiers', 'product_id', string='Modifiers')
    modifier_grp_ids = fields.Many2many('modifiers.groups', string="Modifiers Groups")
    allow_portion = fields.Boolean('Display Pieces?')
    slice_product_ids = fields.Many2many(
        'product.template', 'product_template_slice_rel', 'tmpl_id', 'slice_tmpl_id',
        string="Sides Products")

    def get_modifier(self):
        product_model = self.env['product.product']
        for template in self:
            modifier_templates = template.modifier_grp_ids.mapped('modifier_ids')
            variants = product_model.search([
                ('product_tmpl_id', 'in', modifier_templates.ids),
            ])
            existing_by_variant = {
                line.name.id: line
                for line in template.modifier_ids
            }
            template.modifier_ids.filtered(
                lambda line: line.name not in variants
            ).unlink()
            commands = [
                Command.create({
                    'name': variant.id,
                    'desc': variant.display_name,
                    'price': variant.lst_price,
                })
                for variant in variants
                if variant.id not in existing_by_variant
            ]
            if commands:
                template.write({'modifier_ids': commands})

    @api.model
    def _load_pos_data_fields(self, config):
        fields_to_load = super()._load_pos_data_fields(config)
        # Needed in the POS product grid to hide modifier (add-on) products.
        if fields_to_load and 'use_as_modifier' not in fields_to_load:
            fields_to_load = [*fields_to_load, 'use_as_modifier']
        return fields_to_load

    @api.model
    def _load_pos_data_search_read(self, data, config):
        products = super()._load_pos_data_search_read(data, config)
        loaded_templates = self.browse([product['id'] for product in products])
        modifier_templates = loaded_templates.mapped(
            'modifier_ids.name.product_tmpl_id'
        )
        side_templates = loaded_templates.mapped('slice_product_ids')
        dependency_templates = (modifier_templates | side_templates).filtered(
            lambda template: template.active
            and (not template.company_id or template.company_id == config.company_id)
        )
        missing_templates = dependency_templates - loaded_templates
        if missing_templates:
            products.extend(self._load_pos_data_read(missing_templates, config))
        return products


class ProductProduct(models.Model):
    _inherit = 'product.product'

    def get_modifier(self):
        return self.mapped('product_tmpl_id').get_modifier()

    @api.model
    def _load_pos_data_fields(self, config):
        fields_to_load = super()._load_pos_data_fields(config)
        return list(dict.fromkeys([
            *fields_to_load,
            'is_modifier',
            'use_as_modifier',
            'modifier_ids',
            'modifier_grp_ids',
            'slice_product_ids',
            'allow_portion',
        ]))
