"""Normalize the newly introduced payment-source discriminator."""


def migrate(cr, version):
    cr.execute("SELECT to_regclass('public.bs_delivery_payment_binding')")
    if not cr.fetchone()[0]:
        return
    cr.execute(
        """
        UPDATE bs_delivery_payment_binding
           SET external_source = CASE
                   WHEN BTRIM(COALESCE(external_source, '')) = '' THEN 'default'
                   ELSE LOWER(BTRIM(external_source))
               END
         WHERE external_source IS NULL
            OR external_source != LOWER(BTRIM(external_source))
            OR BTRIM(external_source) = ''
        """
    )
