# -*- coding: utf-8 -*-
from odoo import fields, models


class PosConfigInherit(models.Model):
    _inherit = 'pos.config'

    default_partner_id = fields.Many2one('res.partner', string="Select Customer")

    def get_limited_partners_loading(self, offset=0):
        partner_ids = super().get_limited_partners_loading(offset)
        if self.default_partner_id and (self.default_partner_id.id,) not in partner_ids:
            partner_ids.append((self.default_partner_id.id,))
        return partner_ids


class ResConfig(models.TransientModel):
    _inherit = 'res.config.settings'

    pos_default_partner_id = fields.Many2one('res.partner', related="pos_config_id.default_partner_id", readonly=False, string="Default Customer")
