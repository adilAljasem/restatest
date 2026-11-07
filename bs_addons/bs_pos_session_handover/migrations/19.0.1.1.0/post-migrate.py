# -*- coding: utf-8 -*-
"""Back-fill opener / closer data on orders created before 19.0.1.1.0.

Best effort: the opener is the order's current session and cashier, and for
finalized orders the closer is the same session.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE pos_order o
           SET pino_opened_session_id = o.session_id,
               pino_opened_config_id = s.config_id,
               pino_opened_employee_id = o.employee_id,
               pino_opened_at = o.create_date
          FROM pos_session s
         WHERE s.id = o.session_id
           AND o.pino_opened_session_id IS NULL
    """)
    cr.execute("""
        UPDATE pos_order o
           SET pino_opened_by_name = e.name
          FROM hr_employee e
         WHERE e.id = o.employee_id
           AND o.pino_opened_by_name IS NULL
    """)
    cr.execute("""
        UPDATE pos_order o
           SET pino_closed_session_id = o.session_id,
               pino_closed_config_id = o.config_id,
               pino_closed_employee_id = o.employee_id,
               pino_closed_by_name = o.pino_opened_by_name,
               pino_closed_at = o.date_order
         WHERE o.state IN ('paid', 'done', 'invoiced')
           AND o.pino_closed_session_id IS NULL
    """)
    cr.execute("""
        UPDATE pos_order
           SET pino_closed_by_other = (pino_opened_session_id IS DISTINCT FROM pino_closed_session_id)
         WHERE pino_closed_session_id IS NOT NULL
    """)
