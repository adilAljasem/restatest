"""Backfill bilingual product mapping fields introduced in 19.0.2.6.0."""


def migrate(cr, version):
    cr.execute("SELECT to_regclass('public.bs_delivery_product_binding')")
    if not cr.fetchone()[0]:
        return
    cr.execute(
        """
        UPDATE bs_delivery_product_binding
           SET external_name_en = NULLIF(BTRIM(external_name), '')
         WHERE NULLIF(BTRIM(external_name_en), '') IS NULL
           AND NULLIF(BTRIM(external_name), '') IS NOT NULL
        """
    )
    cr.execute(
        """
        UPDATE bs_delivery_product_binding
           SET external_name = NULLIF(BTRIM(external_name_en), '')
         WHERE NULLIF(BTRIM(external_name), '') IS NULL
           AND NULLIF(BTRIM(external_name_en), '') IS NOT NULL
        """
    )
    cr.execute(
        """
        UPDATE bs_delivery_product_binding
           SET platform_price = 0
         WHERE platform_price IS NULL
        """
    )
