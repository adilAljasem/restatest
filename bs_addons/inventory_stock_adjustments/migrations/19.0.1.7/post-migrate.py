def migrate(cr, version):
    # Historical company-wide stock cannot be reconstructed from a local count.
    # Keep the exact previously displayed value for inventories already validated.
    cr.execute("""
        UPDATE stock_inventory_line AS line
           SET validated_quantity = COALESCE(line.location_quantity, 0),
               quantity_frozen = TRUE
          FROM stock_inventory AS inventory
         WHERE line.stock_inventory_id = inventory.id
           AND inventory.state = 'done'
           AND NOT COALESCE(line.quantity_frozen, FALSE)
    """)
