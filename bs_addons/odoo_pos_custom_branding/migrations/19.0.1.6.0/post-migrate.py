# -*- coding: utf-8 -*-
"""19.0.1.6.0: the receipt fills the 80 mm paper (576 dots, like the kitchen
ticket) instead of Odoo's narrower 512-dot image with wide margins."""


def migrate(cr, version):
    if not version:
        return
    cr.execute("UPDATE pos_config SET receipt_print_width = '576' WHERE receipt_print_width = '512'")
