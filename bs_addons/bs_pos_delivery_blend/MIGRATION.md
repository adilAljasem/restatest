# Replacing `bs_pos_blend_intgeration`

The old addon is deliberately not a dependency and is not modified by this
connector. Use a database backup and perform the replacement in this order:

1. Install/upgrade `bs_pos_delivery_core`.
2. While `bs_pos_blend_intgeration` is still installed, install
   `bs_pos_delivery_blend`. Its post-init hook creates one Core connection and
   store binding per enabled legacy POS, encrypts the copied tokens, mirrors
   existing Blend/POS order bindings to the canonical order model, and copies
   legacy log rows to the shared audit log.
3. Verify every migrated connection, branch, secret, URL, and order binding.
   Send an authenticated non-production test to `/api/blend/webhook`.
4. Uninstall `bs_pos_blend_intgeration` immediately after verification, then
   upgrade `bs_pos_delivery_blend` and restart all Odoo workers. Keeping both
   installed long-term leaves two controllers declaring the same compatibility
   URL; their response contract is the same, but routing ownership is undefined.
5. Keep `bs_pos_delivery_blend` and its Core dependency installed.

Upgrading Blend to 19.0.1.10.0 copies the existing order-status/menu-event URL
into **Blend Outbound Webhook URL** and selects the POS `order.updated` schema.
If the two old URLs differ, the upgrade stops so an administrator can configure
one shared destination before retrying. The optional Get Menu URL remains separate.

The replacement retains legacy technical fields on POS, products, categories,
and POS orders. Blend menu toppings use Bunbastic's
`pos_restaurant_combo_modifiers` groups and options. Upgrade Blend and synchronize
the menu again to assign these groups and remove Blend-created combo choices
from products. The old `product_modifiers` addon is not required by Blend.
Incoming modifier quantity and total are per parent item; update
the Blend sender before enabling this version. Do not change a historical order's
header total unless it matches the amount actually charged. For new imported
orders, Blend's validated modifier total determines the POS modifier line price
even when its Bunbastic catalogue option price differs. The provider-specific
`blend.integration.log` table and cron belong only to the old addon; after the
copy they may be removed by normal Odoo uninstall. From that point onward,
Core's shared webhook, outbox, binding, canonical order, and log models are the
only operational records.

## Known migration boundaries

- Legacy configuration was branch-scoped. The hook therefore creates separate
  Core connections even when several branches happen to share credentials;
  administrators may consolidate them later after checking remote IDs.
- Product/category/modifier availability fields remain global for wire
  compatibility. Product identity is additionally scoped per connection in
  `bs.delivery.product.binding`.
- Core's outbox remains order-binding scoped. Local product create, edit, and
  deletion now queue `menu.updated` on Core's menu synchronization version model
  for active Blend connections with an outbound webhook URL. A Blend-only cron sends
  these notifications after commit and retries temporary failures. Inbound
  `menu.updated` still performs the authenticated menu pull. Edits to a
  Blend modifier group also queue its parent products. Category edits do
  not yet create automatic outbound notifications.
  No second provider-specific queue table is introduced.
- A failed historical log is copied for audit only; it is not re-queued. Retry
  live work from the Core outbox after migration.
