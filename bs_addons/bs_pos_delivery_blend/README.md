# BS POS Delivery Blend

This Odoo 19 connector replaces `bs_pos_blend_intgeration` with a provider
adapter built on `bs_pos_delivery_core`.

The legacy webhook route and response shape remain available:

```text
POST /api/blend/webhook
Authorization: Bearer <inbound-token>
Content-Type: application/json
```

The route keeps the original HTTP routing flags, status codes, `status`,
`message`, `event`, branch counters, `result`, and per-response `trace_id`.
It accepts the original primary events (`menu_sync`, `item_availability`,
`modifier_availability`, `branch_availability`, `order_updated`) and POS aliases
(`menu.updated`, `item.updated`, `store.updated`, `order.updated`).

## Configuration

Create a shared **Delivery Connection** for provider **Blend**, link its POS
branches, and set a Blend Branch ID on every linked POS. Store the legacy
inbound Bearer token in **Webhook Secret** and the outbound token in **Access
Token**. Configure one **Blend Outbound Webhook URL** for order, menu, item and
store events, plus the optional **Blend Get Menu URL** on the same
connection. No Blend-specific top-level menu or operational table is created.

For imported orders, enable **Use Presets** on each POS branch and add its
Delivery, Pickup, and Dine In presets to **Available Presets**. In the Blend
connection's **Branches & Work Time** tab, select the corresponding preset on
each branch mapping. An unset mapping keeps the branch's existing default
preset. Preset selection does not override the imported order's prices, taxes,
pricelist, or fiscal position.

Select **Menu Pricelist** under **Menu & Pricing** on each connection. Its prices
are used for outbound menu products and product-priced modifier options; fixed
modifier and combo extras keep their configured values. A legacy POS token must
resolve to a single Blend connection for a menu response. An ambiguous token or
missing/archived menu pricelist returns HTTP 409. The response fields and
accounting rules for orders without modifiers remain unchanged.

Incoming requests require the configured Bearer token and a mapped branch. Outgoing
requests use the connection's provider credentials through Core's shared HTTP client.

Local product changes that affect the Blend catalogue enqueue a `menu.updated`
notification for connected Blend branches. The Blend menu notification cron sends
it after the product transaction commits; failed attempts remain visible on the
menu synchronization record. The notification asks Blend to refresh the menu;
the menu API still calculates prices from the connection's Menu Pricelist.

Order identities are stored in `bs.delivery.order.binding`, operational order
state is mirrored to `bs.delivery.order`, requests use `bs.delivery.outbox`, and
all attempts use `bs.delivery.sync.log`. Inbound deliveries also reserve a
`bs.delivery.webhook.event` for idempotency.

Blend menu synchronization uses Bunbastic's `pos_restaurant_combo_modifiers`.
Each topping becomes a `pos.modifier.group` with `pos.modifier.option` choices
assigned to its menu product, which remains a regular POS item. Incoming `modifiers[].quantity` and
`modifiers[].total` are per parent item: a product quantity of 3 with a modifier
quantity of 2 and modifier total of 2 creates a modifier POS line with quantity
6 and total 6. The header total must equal the sum of product totals plus each
modifier total multiplied by its parent quantity. Modifier POS lines retain the
parent reference and the unscaled selection quantity in Bunbastic's native fields.
For imported Blend orders, the validated modifier amount sets the POS line price
even when the Bunbastic option has a different catalogue price. The modifier
selection, quantity, tax, and discount checks still apply; regular POS orders
continue to use the configured option price.

## Kitchen printing

When automatic kitchen printing is enabled on a Blend connection or branch,
each incoming order receives one automatic print attempt. Once a POS device
claims the ticket, its delivery order shows **Printing**. A successful device
acknowledgement changes it to **Sent to Kitchen**; a failed acknowledgement
changes it to **Print Error** and records the printer error. POS refreshes and
order status changes do not retry failed Blend tickets. Other providers keep
their existing Core retry policy.

A delivery manager can open the delivery order and use **Retry Kitchen Print**
after checking whether the ticket was already printed. This also handles a
ticket left in **Printing** after a browser or network interruption, once its
POS claim has expired. The button requests one further attempt and keeps the
cumulative attempt count. The original automatic attempt still requires a
running POS device and a reachable kitchen printer.

See [MIGRATION.md](MIGRATION.md) before removing the legacy addon.

## Branding

The supplied Blend logo is retained in `static/img/img.png`. An unchanged copy
at `static/description/icon.png` is the module icon and the source for the
Core provider card, connection avatar, and POS provider logo.

After updating the files, restart Odoo, update the Apps list, upgrade
`bs_pos_delivery_core`, and reopen **Settings > Provider Catalog**. The catalog
replaces an old Odoo placeholder even when Blend is not installed, without
overwriting a customized provider logo. Installing or upgrading Blend also
registers its module icon through the existing provider registration.
