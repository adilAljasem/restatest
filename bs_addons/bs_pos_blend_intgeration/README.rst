POS Blend Integration
=====================

This Odoo 19 add-on synchronizes POS menu data, availability, and order
status with Blend. It implements the primary Blend API schema and the
alternate POS event schema included in the supplied API document.

Configuration
-------------

Open ``Point of Sale → Configuration → Point of Sale``, select a POS, then
open the ``Blend Integration`` tab. The tab is visible to system
administrators only.

Required values:

* Enable Blend Integration.
* Blend Branch ID.
* Blend Brand ID.
* A dedicated Blend Login User for the production ``POST /api/login`` flow,
  or an Inbound Bearer Token only for legacy pre-shared-token integrations.
* Outbound Bearer Token.
* Blend Order Created URL.
* Blend Order Status URL.

Optional values:

* Blend Menu Event URL for POS-to-Blend ``menu.updated`` notifications.
* Timeout, maximum retry attempts, image synchronization, and outgoing
  status schema.

Authentication and inbound URLs are::

    POST https://<odoo-host>/api/login
    POST https://<odoo-host>/api/blend/webhook
    GET  https://<odoo-host>/api/menu
    GET  https://<odoo-host>/api/branches
    POST https://<odoo-host>/api/orders
    PUT  https://<odoo-host>/api/order/update

``/api/login`` accepts the exact documented JSON body::

    {
      "email": "pos_user@example.com",
      "password": "your_password"
    }

and returns::

    {"access_token": "..."}

The module provisions an active Portal service account named ``Blend User``
with login ``blend_user``. It is proposed automatically when Blend is enabled;
the upgrade migration also assigns it to enabled configurations that have a
Brand ID and no login user. Set a strong, environment-specific password on this
user after installation. No default password is shipped in source control.
Odoo stores the password using its normal password hashing; the POS configuration
never stores the plaintext password. A successful login rotates the opaque access
token and stores only its SHA-256 digest. Use ``Revoke Login Token`` to invalidate
it immediately.

The single inbound webhook URL is::

    https://<odoo-host>/api/blend/webhook

Send the access token returned by ``/api/login`` as
``Authorization: Bearer <token>``. On a
multi-database development server without a database filter, also send
``X-Odoo-Database``. A production domain should normally select its database
through Odoo's ``dbfilter``.

Where to enter email and password in Postman
--------------------------------------------

Import ``doc/Blend_Sandbox.postman_collection.json`` and open the collection's
``Variables`` tab. Set ``pos_login_email`` and ``pos_login_password`` there.
Run ``00 - Authorization (Required) / 00 - Login with Email & Password`` first.
Its test script saves ``access_token`` into ``odoo_inbound_token`` automatically.
Every request in the two Blend-to-Odoo folders then inherits
``Authorization: Bearer {{odoo_inbound_token}}``. Do not put the email/password
in the request's Authorization tab; they belong only in the JSON body of
``POST /api/login``.

Manual POS endpoints
--------------------

The current Blend Manual POS contract is implemented as follows:

* ``GET /api/menu`` returns categories, products, modifiers, branch stock, and
  pagination. Category, product, modifier-group, and modifier IDs are JSON
  numbers. It supports ``page``, ``per``, ``filter['product_id']``,
  ``filter['category.id']``, and ``filter['modifier_group.id']``.
* ``GET /api/branches`` returns all branches authorized by the login token.
* ``POST /api/orders`` requires the published routing fields ``order_type``,
  ``aggregator``, and ``aggregator_order_id`` in addition to branch, brand,
  partner reference, totals, status, and products. It validates product totals,
  modifier limits, schedules, and availability before creating one draft POS
  order in the currently open session. The routing fields and original payload
  are stored on the order. Because Blend does not send a separate discount,
  Odoo derives it as ``subtotal + tax - total`` and stores it for audit without
  subtracting it from the already-final product totals a second time.
  ``partner_reference`` is idempotent per branch, so a safe retry cannot create
  a duplicate order. ``customer`` remains optional.
* ``PUT /api/order/update`` finds the POS order by its returned ``order_id`` and
  applies the documented status safely.

All four endpoints require the Bearer token returned by ``POST /api/login``.
Incoming order creation and updates use database savepoints, and identical
requests are serialized with a PostgreSQL transaction lock to protect against
concurrent retries.

Financial acceptance (opt-in, Odoo 19)
--------------------------------------------------

In ``Blend Integration / Blend Payments and Invoicing``:

Before receiving new orders, enable POS presets and configure the per-type
record links on the Blend Integration tab: ``Blend Delivery Preset`` for
``delivery``, ``Blend Pickup Preset`` for ``pickup`` (usually Take Away), and
``Blend Dine-in Preset`` for ``dinein``. Each selected preset must be an available
sale preset on that POS. Names and translations are not used for matching.
Case, whitespace, hyphens and underscores are normalized in the incoming type;
``blend_order_type`` preserves the original value. The previous default preset
field retains its selected record for delivery only. Configure other mappings
before receiving those types. Unused types may remain unconfigured.
Unknown types and missing/invalid mappings return HTTP 409 without falling back
to the cashier's default. Retries of already imported orders return the original
order. Existing orders and the cashier's default preset are unchanged.

1. Configure the POS sales invoice journal, product income accounts and actual
   Odoo sales taxes/fiscal positions. The connector never creates accounting
   accounts, journals or synthetic tax rates from the incoming totals.
2. Add ``Blend Aggregator Payments`` mappings, for example ``jahez`` to the
   POS payment method used for Jahez settlements. Mappings are per POS and
   company. Matching ignores case and surrounding whitespace only. A missing
   mapping is an error, never a fallback to Cash. Archived mappings must be
   reactivated rather than duplicated.
3. Use an active cash/bank payment method enabled on that POS, in the same
   company and currency. Pay Later, terminals and online collection methods
   are deliberately unsupported. This records accounting settlement; it does
   **not** charge a gateway or confirm receipt of money from the aggregator.
4. Set ``Default Blend Invoice Customer`` if requests can omit customer data.
   An incoming customer takes precedence. Use a real, appropriate customer
   and configure any invoice/localization details required by your company.
5. Enable ``Pay and Invoice on Blend Acceptance``. It defaults to off and
   enrolls **newly imported** orders only. Existing orders and normal POS orders
   are not migrated or charged. Enable ``Auto Accept Blend Orders`` separately
   if accounting should run during order creation without a manual decision.

Local ``Accept Order`` uses Odoo's native backend payment wizard: a mapped
``pos.payment``, paid checks, stock picking, posted customer invoice, payment
journal entry and reconciliation. No additional ``account.payment`` is
manufactured. Order, invoice, tax, paid amount and invoice residual must agree
before acceptance is queued to Blend. A free order creates an invoice but no
fictitious nonzero payment. Native session closing remains responsible for its
normal POS accounting. PDF/email generation is not forced during acceptance.

For enrolled orders, product and modifier totals are already-discounted,
tax-inclusive **extended line totals**; quantity is not multiplied again and
modifiers are separate lines. Native Odoo product taxes and fiscal-position
mapping determine the tax allocation. ``subtotal + tax - total`` remains an
audit discount, not a second deduction. If the payload tax/total cannot be
represented with the configured taxes and rounding, accounting fails closed.
This normalization does not change the legacy feature-off import path.

The original financial lines, branch/session, customer and payload are locked
against ordinary operator edits. Use the Blend acceptance action instead of
the generic Payment button. Correct product/configuration setup before retrying;
do not edit financial snapshots or add partial payments directly.

Accounting and the acceptance outbox entry share one database savepoint. On
failure, partial payments/invoices/pickings and acceptance notifications roll
back; the received order and diagnostic remain. ``Blend Accounting`` shows the
error and ``Retry Acceptance and Accounting`` retries after configuration is
corrected. Retrying the original create request returns the same order, not a
new accounting attempt. Outbound HTTP retries never rerun accounting.
PostgreSQL serialization/deadlock failures propagate to Odoo's request retry
layer. Import/acceptance lock the order/session and fence session closing.

Incoming remote status callbacks never independently charge an order. If Blend
already advanced it to Accepted/Preparing/etc., ``Complete Blend Accounting``
performs local accounting while preserving that remote state and without
emitting an older/duplicate ACCEPTED. Automatic financial acceptance is for a
new Pending order, not an arbitrary remote status update.

Cancellation after invoicing flags ``Blend Cancellation Requires Refund
Review`` and records a chatter warning. It does not delete payments, cancel
posted invoices or refund money automatically. A manager must use the standard
POS refund/credit-note process and confirm the external settlement policy.

Validate locally with test tag ``blend_accounting`` and then the complete
module suite. Before enabling in production, a separate authorized acceptance
test must verify the actual chart of accounts, tax rounding, localization/EDI
(including Saudi e-invoicing if installed), session close and Blend delivery.
The generic local suite does not certify a live tax authority or payment gateway.

Public product images
---------------------

The menu's ``image_url`` points to
``GET /api/blend/products/<product_template_id>/image?v=<image_checksum>``.
This image-only endpoint needs no login, cookie or Bearer token. It uses
``sudo()`` only after checking that the product belongs to the catalogue of
at least one enabled Blend POS, using the same company, category and branch
rules as ``GET /api/menu``. Out-of-stock products still in the menu retain
their pictures; removing a product from all enabled menus revokes access on
the next fetch. Other product fields and attachment access are not made public.

The endpoint streams only ``image_1920``, ignores caller-supplied model/field
parameters and returns 404 for missing/inaccessible images rather than a
placeholder. The checksum changes the URL when the image changes. Configure
``web.base.url`` with the externally reachable HTTPS Odoo domain. After upgrading,
refresh the menu in Blend to replace previously stored ``/web/image`` URLs.

Events
------

Inbound primary events:

* ``menu_sync``
* ``item_availability``
* ``modifier_availability``
* ``branch_availability``
* ``order_updated``

Inbound alternate POS events:

* ``item.updated``
* ``store.updated``
* ``order.updated``

``item.updated.is_stock`` and ``store.updated.status`` must be JSON booleans;
quoted strings and numeric ``0``/``1`` values are rejected.

Outbound events:

* ``order_created``
* ``order_updated`` or ``order.updated``, according to the configured schema
* ``menu.updated``, from POS to Blend only, when an outbound menu-event URL is
  configured. Blend then calls Odoo's ``GET /api/menu`` endpoint.

Menu-change notifications use the same product selection as ``GET /api/menu``:
saleable POS products, company and category restrictions, and optional Blend
branch mapping. Native Odoo products do not need a Blend ID or a branch mapping;
their Odoo template ID is used, just as in the menu response.

Creation, menu-relevant edits (including name, description, price, image and
availability), archival and deletion queue notifications for affected enabled
branches with a configured ``Blend Menu Event URL``. Membership or ID changes
notify both the previous and new targets. Category/group edits are covered;
modifier option changes invalidate the parent products that include them.
Internal-only changes, such as an internal reference, do not notify Blend.

Notifications are queued in the same transaction as the edit and sent by the
existing outbound cron with retries. Failed/rolled-back edits produce no
notification, inbound Blend synchronization is not echoed back, and independent
saves within the same second remain separate events. No outbound HTTP request
is made while saving a product.

The ``order_id`` returned by Blend after ``order_created`` is saved in
``pos.order.blend_order_id`` for diagnostics and inbound lookup. Outbound
status messages keep using the original Odoo UUID/reference sent in
``order_created``, as required by the webhook contract.

Branch isolation and live visibility
------------------------------------

Imported products, categories, and modifier groups are assigned to the POS
configuration that owns the Blend branch. A full sync or availability event
therefore cannot deactivate another branch's menu. Flat ``item.updated``
events for existing SKU products are also stored per POS configuration.

Branch, category, and item schedules are evaluated both by the server and by
the open POS every 30 seconds. Hidden products stay loaded in the POS data so
they can become visible again after an availability event or schedule boundary
without requiring a page reload. Modifier groups enforce shared minimum and
maximum selection limits, and each option enforces ``max_per_quantity``.
``max_selection`` and ``max_per_quantity`` are always positive integers;
optional groups use ``min_selection = 0`` and still have ``max_selection >= 1``.

Retry and diagnostics
---------------------

All incoming and outgoing JSON is stored in
``Point of Sale → Configuration → Blend Integration → Integration Logs``.
Blend IDs are stored directly on ``product.template``, ``pos.category``, and
the native ``modifiers.groups`` records supplied by ``product_modifiers``.
There are no separate product, category, modifier-group, or modifier-option
mapping models. Blend fields, tokens, logs, and manual retry actions are
restricted to system administrators.

Outbound calls are queued and retried after approximately 1, 5, 15, 60, and
360 minutes. The configured maximum attempt count controls when an operation
becomes permanently failed. A non-2xx response, ``status=failed``, timeout,
network error, invalid URL, or invalid Blend success body enters this retry
flow.

Each log records:

* request and response payloads;
* masked request headers;
* HTTP status;
* attempt count and next retry time;
* start/completion times;
* precise duration in milliseconds;
* error message and idempotency key.

Verification
------------

Run the automated Odoo tests::

    D:\odoo\odoo19\.venv\Scripts\python.exe D:\odoo\odoo19\odoo\odoo-bin -c D:\odoo\odoo19\odoo.conf -d <qa_database> -u bs_pos_blend_intgeration --test-enable --test-tags /bs_pos_blend_intgeration --stop-after-init --log-level=test

Use an isolated local QA database. The existing menu serializer requires the
Arabic language (``ar_001``) to be active; the existing schedule test assumes
the QA company's timezone is UTC. Do not change production timezone settings
to satisfy that test. Focused notification coverage is in
``TestBlendMenuNotifications``; its outbound HTTP calls are mocked.

Run a real inbound HTTP smoke test using the documented login flow. Keep the
password out of shell history::

    $env:BLEND_POS_PASSWORD = '<password>'
    D:\odoo\odoo19\.venv\Scripts\python.exe bs_pos_blend_intgeration\scripts\blend_smoke_test.py --url https://<odoo-host>/api/blend/webhook --database <database> --branch <branch_id> --brand <brand_id> --email <service-user-email>

The smoke script also accepts ``--token`` for a legacy pre-shared token.

Add ``--order-id <blend_order_id>`` to exercise both incoming order-status
schemas against an existing POS order.

Sandbox checklist
-----------------

Before running the smoke test:

* ask Blend to whitelist the public outbound IP of the Odoo server;
* create and map a dedicated Blend Login User;
* configure the branch and brand IDs, the outbound Bearer token, and the HTTPS URLs;
* make the Odoo webhook URL publicly reachable over HTTPS;
* configure a strict production ``dbfilter`` so Blend does not need a database header;
* terminate TLS at the reverse proxy and rate-limit ``/api/login``;
* set ``proxy_mode = True`` when Odoo is behind that trusted reverse proxy;
* keep ``blend.allow_insecure_urls`` unset or equal to ``0``;
* run at least one cron thread so the retry queue is processed
  (``max_cron_threads >= 1``);
* select the outgoing status schema confirmed by Blend;
* run the automated tests, then the smoke script, then verify the Integration
  Logs contain successful inbound and outbound entries.

The complete, single-file Postman sandbox collection is available at
``doc/Blend_Sandbox.postman_collection.json``. Import it, then fill its
``Variables`` tab; inbound and outbound Bearer authentication is configured
on the relevant folders.

The latest real database verification is recorded in
``doc/PINO2_ENDPOINT_TEST_REPORT.md``.
