# -*- coding: utf-8 -*-
"""19.0.1.5.0: order numbers are always 3 digits (001 ... 999)."""


def migrate(cr, version):
    if not version:
        return
    cr.execute("UPDATE pos_config SET order_number_end = 999 WHERE order_number_end > 999")
    cr.execute("UPDATE pos_config SET order_number_start = 1 WHERE order_number_start < 1")
    cr.execute("""
        UPDATE pos_config SET order_number_start = 1
         WHERE order_number_range_enabled AND order_number_end <= order_number_start
    """)
