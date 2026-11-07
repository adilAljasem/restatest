"""Move the two old outbound destinations to the single Blend webhook URL."""


def migrate(cr, version):
    cr.execute("""
        SELECT connection.id
          FROM bs_delivery_connection AS connection
          JOIN bs_delivery_provider AS provider ON provider.id = connection.provider_id
         WHERE provider.code = 'blend'
           AND NULLIF(BTRIM(connection.blend_status_url), '') IS NOT NULL
           AND NULLIF(BTRIM(connection.blend_event_url), '') IS NOT NULL
           AND BTRIM(connection.blend_status_url) <> BTRIM(connection.blend_event_url)
         LIMIT 1
    """)
    conflict = cr.fetchone()
    if conflict:
        raise ValueError(
            "Blend connection %s has different status and event webhook URLs; "
            "set both to the same destination before upgrading." % conflict[0]
        )

    cr.execute("""
        ALTER TABLE bs_delivery_connection
        ADD COLUMN IF NOT EXISTS blend_outbound_webhook_url varchar
    """)
    cr.execute("""
        UPDATE bs_delivery_connection AS connection
           SET blend_outbound_webhook_url = COALESCE(
               NULLIF(BTRIM(connection.blend_status_url), ''),
               NULLIF(BTRIM(connection.blend_event_url), '')
           )
          FROM bs_delivery_provider AS provider
         WHERE provider.id = connection.provider_id
           AND provider.code = 'blend'
           AND NULLIF(BTRIM(connection.blend_outbound_webhook_url), '') IS NULL
    """)
    cr.execute("""
        UPDATE bs_delivery_connection AS connection
           SET blend_schema = 'pos'
          FROM bs_delivery_provider AS provider
         WHERE provider.id = connection.provider_id
           AND provider.code = 'blend'
           AND connection.blend_schema <> 'pos'
    """)
