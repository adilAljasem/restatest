"""Require explicit collector review of mappings created before Core v1."""


def migrate(cr, version):
    cr.execute("SELECT to_regclass('public.bs_delivery_payment_binding')")
    if not cr.fetchone()[0]:
        return
    cr.execute(
        """
        UPDATE bs_delivery_payment_binding
           SET collector_scope = 'unassigned'
         WHERE collector_scope IS NULL
        """
    )
