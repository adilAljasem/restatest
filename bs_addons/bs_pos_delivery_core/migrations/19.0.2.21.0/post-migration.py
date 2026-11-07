def migrate(cr, version):
    """Raise the tax rounding tolerance of existing connections to 5 SAR.

    Only connections still on the first default (0.05) are changed; a value
    chosen by hand is kept.
    """
    if not version:
        return
    cr.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'bs_delivery_connection' AND column_name = 'tax_rounding_tolerance'"
    )
    if not cr.fetchone():
        return
    cr.execute(
        "UPDATE bs_delivery_connection SET tax_rounding_tolerance = 5.0 "
        "WHERE tax_rounding_tolerance IS NULL OR tax_rounding_tolerance = 0.05"
    )
