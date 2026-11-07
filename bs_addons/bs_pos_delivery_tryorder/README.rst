BS POS Delivery TryOrder
========================

Odoo 19 TryOrder connector using ``bs_pos_delivery_core``. The legacy
``/order_connector`` HTTP paths remain available for TryOrder clients, while
Core owns the order, POS, payment, status, POS board, and kitchen print workflow.

Setup
-----

* Install ``bs_pos_delivery_tryorder`` alongside Core and
  ``pos_restaurant_combo_modifiers``.
* Create a TryOrder provider connection in Delivery Core. Copy the existing
  ``order_connector.api_key`` value into its incoming ``webhook_secret`` for
  the unchanged ``X-Api-Key`` header, then set the TryOrder HTTPS gateway
  ``base_url``, and the outgoing ``access_token`` for ``X-Webhook-Secret``.
* To test the API before connecting the gateway, set Environment to Test and
  enable **Allow TryOrder API Testing While Draft** on the connection. An active
  Draft connection then accepts requests with its incoming ``webhook_secret``;
  Production Draft connections remain blocked. Test order requests create real
  Odoo records in the selected database.
* Link each POS configuration with a Core store binding. The legacy
  ``branch_id`` value is the POS configuration ID.
* Configure Core payment mappings for TryOrder ``cash`` and ``credit_visa``;
  add ``wallet`` when TryOrder sends wallet tender. Configure a tax-free
  delivery fee product on the store when TryOrder sends ``delivery_fee``.
* Configure the Core acceptance policy and an invoice customer for automatic
  acceptance. POS and Kitchen receive accepted orders through their existing
  Core and Odoo services.
* For production, upgrade this connector so its provider is registered as
  Production Ready, then configure the actual HTTPS TryOrder gateway URL and
  outbound secret. **Test Connection** validates these settings locally; it
  does not send a request to TryOrder. Verify the provider's inbound and
  outbound requests with the real integration before enabling live orders.
* Installed delivery platforms appear in the POS header. An unconnected
  platform remains visible, but its order board becomes available only after
  a connected connection is linked to that POS configuration.

API
---

All routes except the signed product image URL require the connection's
``X-Api-Key``. Requests are scoped to active TryOrder connections and their
linked POS branches.

===============================================  ===============================
Path                                             Purpose
===============================================  ===============================
``GET /order_connector/ping``                    Validate connection credentials
``GET /order_connector/branches``                List authorized POS branches
``GET /order_connector/catalog?branch_id=ID``   Export branch menu and option IDs
``POST /order_connector/orders``                 Receive ``{"order": {...}}``
``PUT /order_connector/orders/ID/status``        Apply a provider status update
===============================================  ===============================

Order payloads retain the original ``provider_order_id``, ``branch_id``,
``items`` (template ``product_id``, ``qty``, ``price``), ``modifiers``
(``id``, ``qty``, ``price``), ``delivery_fee``, ``wallet_amount`` or wallet
components within ``discounts``, ``payment_type``, ``customer``, and
``order_type`` fields. Catalog modifier IDs are ``posmod-ID``; combo choices
are ``comboitem-ID``; legacy attribute options ``attr-ID`` and ``ptav-ID``
also remain available. A modifier quantity is per parent item. Product options
are child lines; instruction options become structured kitchen instructions
without a substitute product. Core verifies product, tax, fee, and payment
totals before POS acceptance.

When POS is created, the legacy response returns ``order_id`` as
``pos.order.id``, ``order_name`` as its POS name, its POS ``state``, and
``kind=pos``. Status updates accept the same POS ID. If no POS session is
available, Core retains the order and returns ``kind=core`` with its Core ID
until POS can resume; unlike the old connector, no Sale order is created.
The API key and ``/order_connector`` paths stay unchanged. Product images use
signed branch-scoped URLs instead of the old public URLs.

Modifier catalogue pricing and availability
------------------------------------------

Native ``posmod-ID`` options use the effective settings on their parent product.
``Included`` costs zero, ``Fixed Extra Price`` uses the configured extra, and
``Product Pricelist Price`` uses the linked product variant's price for one unit
from the TryOrder connection's **Menu Pricelist**. A product-priced modifier
requires an active, valid menu pricelist; otherwise Catalog returns HTTP 409.
TryOrder POS orders use that same Menu Pricelist when revalidating modifier
prices, even when the branch has a different POS pricelist.
Fixed and included option prices do not fall back to the product's sale price.

Each native modifier includes a boolean ``status``. Tracked products require
positive on-hand stock minus reservations under the branch POS picking source,
restricted to the branch company and the option's selected product variant.
Untracked products do not require stock. A paused branch or a branch outside
its configured delivery work time marks product options unavailable. The price
is still returned when ``status`` is false, so the client must honor this field
to disable the choice. Instruction options remain zero-price choices without
stock requirements.

Inactive options, product overrides with **Available** disabled, and products
that are archived, not saleable, disabled for delivery, or owned by another
company are omitted. Hidden standalone POS products can still appear as extras.
A modifier group with no available choices is omitted, matching Blend's menu
behavior. Legacy attribute options, combo extras and incoming order amounts
keep their existing contracts. TryOrder uses its own connection and branch
settings and does not require the Blend module.

Financial boundary
------------------

Coupons and loyalty discounts are kept as separate untaxed negative POS and
invoice lines. Core records their labels and amounts separately from product
line discounts. Product prices and taxes remain unchanged. Discounts above
the order total return HTTP 422, as does a declared ``total`` that disagrees
with Odoo's item, tax, fee, and discount amounts. Wallet
value is a collected payment, never a price discount. A wallet payment uses
``wallet_payment_reference`` when provided; otherwise the order's stable
``provider_order_id`` forms its reference. Online card payments similarly use
``payment_reference`` or a stable order-based reference when TryOrder omits it.

For native Odoo combos, TryOrder's base gross amount is allocated across the
selected combo choice lines before Core intake. Each choice's Odoo tax and
fiscal position determine its net amount and tax, while the total remains
unchanged. Core and POS therefore use the same financial lines; the original
provider payload remains on the authenticated webhook event. Core still blocks
acceptance if its POS tax and total check finds a mismatch.

The former ``order_connector`` module was not installed in the local
``aggre`` test database. An existing installation elsewhere needs a separate
data and XML ID migration review before this renamed module replaces it.
