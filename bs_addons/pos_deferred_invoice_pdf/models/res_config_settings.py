from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    pos_defer_invoice_pdf_generation = fields.Boolean(
        related='pos_config_id.defer_invoice_pdf_generation',
        readonly=False,
    )
    pos_invoice_pdf_cron_active = fields.Boolean(
        compute='_compute_pos_invoice_pdf_cron_active',
    )

    @api.depends('pos_config_id')
    def _compute_pos_invoice_pdf_cron_active(self):
        active = self.env.ref('account.ir_cron_account_move_send').sudo().active
        for settings in self:
            settings.pos_invoice_pdf_cron_active = active
