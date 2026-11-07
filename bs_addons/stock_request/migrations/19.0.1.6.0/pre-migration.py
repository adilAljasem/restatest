# Copyright 2026 Mohamed Elmalky
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl.html).


def migrate(cr, version):
    """Map the previous computed order states to the simple workflow."""
    cr.execute(
        """
        UPDATE stock_request_order
           SET state = CASE state
               WHEN 'open' THEN 'ready'
               WHEN 'cancel' THEN 'cancelled'
               ELSE state
           END
         WHERE state IN ('open', 'cancel')
        """
    )
