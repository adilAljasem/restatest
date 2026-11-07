from odoo import fields, models


class PosConfig(models.Model):
    _inherit = 'pos.config'

    kitchen_send_before_payment = fields.Boolean(
        string='Send to Kitchen Before Payment',
        help='Show a "Send" button on the register (outside Bar/Restaurant mode) '
             'to send the order to the preparation printers / display before payment.',
    )
