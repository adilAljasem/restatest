"""Prepare payment mappings for provider sub-source scoping."""

import re


_SAFE_IDENTIFIER = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def migrate(cr, version):
    cr.execute("SELECT to_regclass('public.bs_delivery_payment_binding')")
    if not cr.fetchone()[0]:
        return
    cr.execute(
        """
        SELECT indexname
          FROM pg_indexes
         WHERE schemaname = 'public'
           AND tablename = 'bs_delivery_payment_binding'
           AND indexdef ILIKE '%UNIQUE%'
           AND indexdef ILIKE '%external_payment_code%'
           AND indexdef NOT ILIKE '%external_source%'
        """
    )
    for (index_name,) in cr.fetchall():
        if _SAFE_IDENTIFIER.fullmatch(index_name or ""):
            cr.execute('DROP INDEX IF EXISTS "%s"' % index_name)
