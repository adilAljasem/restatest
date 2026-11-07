# -*- coding: utf-8 -*-
"""Compute the guest stay for table orders that already have opener/closer data."""


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE pos_order
           SET pino_stay_duration = GREATEST(EXTRACT(EPOCH FROM (pino_closed_at - pino_opened_at)), 0) / 3600.0,
               pino_stay_minutes = ROUND(GREATEST(EXTRACT(EPOCH FROM (pino_closed_at - pino_opened_at)), 0) / 60.0)
         WHERE (table_id IS NOT NULL OR pino_split_from_uuid IS NOT NULL)
           AND pino_opened_at IS NOT NULL
           AND pino_closed_at IS NOT NULL
    """)
