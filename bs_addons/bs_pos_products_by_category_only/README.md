# Pino POS Products By Category Only (Odoo 19)

Hides every POS product until the cashier selects a product category.

## What it does
- `PosStore.productsToDisplay` returns `[]` while no category is selected.
- Searching stays inside the selected category (Odoo 19 core clears the
  category via `setSelectedCategory(0)` as soon as a search starts).
- "Search more" (`loadProductFromDB`) adds a `pos_categ_ids in (...)` domain
  for the selected category and its children, and is disabled without one.
- The empty product area shows "Select a product category" instead of the core
  onboarding block — unless the POS has no category at all, in which case the
  core "No Product Yet?" block with Load Sample / Create Manually is kept.
- The category selector's core `productsToDisplay.length > 0` condition is
  removed, otherwise the selector would disappear and no category could ever
  be selected.

## Tested against
Odoo 19.0 (`point_of_sale`), commit 6394ce4.

## Tests
- Hoot unit tests: `static/tests/unit/product_screen.test.js`
- Python runner: `odoo-bin -i bs_pos_products_by_category_only --test-enable`
