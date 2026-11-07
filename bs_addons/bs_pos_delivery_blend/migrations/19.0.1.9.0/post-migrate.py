def migrate(cr, version):
    """Preserve the credential type configured before the Blend selector existed."""
    cr.execute("""
        UPDATE bs_delivery_connection AS connection
           SET blend_login_method = CASE
               WHEN connection.auth_scheme = 'basic' THEN 'credentials'
               ELSE 'bearer'
           END
          FROM bs_delivery_provider AS provider
         WHERE provider.id = connection.provider_id
           AND provider.code = 'blend'
    """)
