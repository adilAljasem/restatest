Product Category Inventory Location
===================================

This module makes the product category the source of the inventory adjustment
location used by its products.

Features
--------

* Adds a company-dependent ``Inventory Location`` to the Logistics section of
  the product category form.
* Synchronizes ``product.template.property_stock_inventory`` on product create,
  category changes, and category location changes.
* Clears product values when the category value is cleared.
* Includes archived products in category-driven updates.
* Enforces company consistency and keeps values isolated by company.
