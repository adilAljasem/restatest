# Blend production checklist

## Odoo

- Install/upgrade `bs_pos_blend_intgeration` and run its complete automated test suite.
- Use a dedicated database role, a fixed `db_name`/`dbfilter`, and `list_db = False`.
- Run Odoo behind a trusted HTTPS reverse proxy with `proxy_mode = True`.
- Keep Odoo bound to a private interface and expose only the reverse proxy.
- Rate-limit `POST /api/login` and the public `/api/*` paths at the proxy/firewall.
- Set `max_cron_threads >= 1`; otherwise the outbound retry queue cannot run.
- Back up both PostgreSQL and the matching Odoo filestore and verify restore regularly.
- Monitor Odoo errors and failed/retry records in Blend Integration Logs.

## Blend POS configuration

- Enable Blend only after Branch ID and Brand ID have been confirmed by Blend.
- Verify the provisioned `Blend User` (`blend_user`) is linked as Blend Login
  User, then set a strong unique password for the target environment.
- **Revoke Login Token** (19.0.1.27.0) requires a system administrator with
  write access to every selected POS configuration and its selected company.
  Checks apply to direct RPC calls too; a forbidden branch rejects the entire
  batch without revoking any token. Only the selected branches' login-token
  hash and issue time are cleared. The login user/password, other branches,
  legacy inbound token and outbound bearer token are not changed; logging in
  again can issue a new token. This action is not a full integration shutdown.
- Configure the outbound Bearer token and every Blend URL supplied for the environment.
- Configure **Blend Outbound Webhook URL** for `menu.updated`, `item.updated`,
  `store.updated`, and `order.updated`; see [UNIFIED_WEBHOOK.md](UNIFIED_WEBHOOK.md).
  Keep legacy `order_created` on its separate confirmed destination. Without
  a unified URL, the old menu/status fields still apply. Blend pulls the menu
  from Odoo's `GET /api/menu`; there is no outbound Get Menu URL.
- Production outbound URLs must use HTTPS. Keep `blend.allow_insecure_urls` unset or `0`.
- The unified URL automatically uses `order.updated`; schema selection applies
  only to legacy routing without that URL.
- **Enable Legacy Order Creation** defaults to off (19.0.1.24.0). Only enable it
  if Blend explicitly requires the separate `order_created` contract and has
  supplied its destination. Turning it off hides the URL without erasing it,
  prevents new jobs, and blocks old waiting jobs at send time. Review those
  blocked logs before enabling and manually retrying; the four unified events
  remain active independently.
- Keep at least one POS session open in every branch that accepts `POST /api/orders`.
- Confirm every Create Order request supplies non-empty string values for
  `order_type`, `aggregator`, and `aggregator_order_id`; `customer` is optional.
- Set the company/branch timezone and verify branch, category, and item schedules.

## Inbound image downloads (19.0.1.29.0)

- This hardening applies only to `menu_sync` importing the first `images[]`
  URL when **Download Blend Images** is enabled. It does not change public
  product image URLs, their access rules, menu exports or outbound webhooks.
- Supply a direct HTTPS URL without embedded username/password. All redirects
  are refused, including public CDN redirects. A Requests response hook rejects
  3xx before its redirect machinery can read the body or prepare another
  destination. Ask the image provider for the final direct URL if necessary.
- Only HTTP 200 with an image Content-Type and decodable raster image data is
  accepted. HTML disguised as an image, SVG, empty/unreadable data and images
  beyond Odoo's resolution limit are skipped. JPEG, PNG, GIF, BMP and WebP
  (when Pillow's WebP codec is available) are covered by regression tests.
  Normal Odoo image processing still applies; process-wide decoder settings
  are not changed. This is not a new image conversion service.
- Keep the existing 5 MiB download limit, checked both against Content-Length
  when supplied and actual streamed bytes. Responses are closed on success and
  failure. The configured connection/read timeout is capped at 20 seconds;
  it is not a total wall-clock download deadline.
- Invalid/unavailable images do not erase the previous image or prevent other
  product data/images from synchronizing. A product without an earlier image
  stays without one. The importer continues to use only the first image URL;
  it does not silently fall back to other URLs. Warnings identify the config
  and a controlled reason without logging signed URLs or exception text.
- **Remaining SSRF limitation:** the existing DNS precheck is separate from
  Requests/urllib3 connection resolution. A socket-mocked audit confirmed that
  a public first answer followed by a loopback second answer can pass the
  precheck and target the loopback address. Disabling redirects alone does not
  close DNS rebinding. No real network request was used in this audit.
  Connection-address pinning (preserving TLS hostname verification) or enforced
  egress restrictions needs a separately approved transport/network change.
  Do not treat this version as comprehensive SSRF protection; keep image
  downloading disabled for untrusted URL sources until that gap is addressed.

## Network and acceptance

- Status delivery hardening (19.0.1.28.0): both `order.updated` and legacy
  `order_updated` use the order lock before HTTP. Distinct outgoing transitions
  have distinct queue keys even within the same second. Consecutive callbacks
  for the same status reuse the existing job (including its failure/success);
  a later distinct transition can get a new job. The stable ACCEPTED/REJECTED
  keys are retained for payment/create replays. No wire fields were added.
- Before sending, a newer queued status or an unequivocally later local order
  state supersedes a stale status job. This is **latest intent**, not a promise
  to deliver every intermediate status. Duplicate successful transitions,
  including pre-upgrade jobs, are not sent again. Order writes on enqueue and
  acknowledgment fence competing REPEATABLE READ snapshots before HTTP.
  Superseded jobs retain their original request, last response and attempt
  audit; they are neither delivered successfully nor eligible for manual retry.
- Explicit JSON `success: false` is rejected even with HTTP 2xx (and even if
  another field says `status: success`). The existing `status: failed` and
  strict `order_created` checks remain. Existing 2xx/empty acknowledgments are
  still compatible: obtain Blend's real successful response before requiring
  a new acknowledgment schema. Queue Success is not proof of downstream
  business processing. A lost acknowledgment or transaction failure after HTTP
  can still cause a retry: receiver-side idempotency remains necessary.
- This change does not rewrite incoming status transitions, tax calculations,
  order/product authorization scope or shared-menu/branch-stock routing. Old
  terminal logs are not reset and old payloads are not rewritten by the upgrade.

- Manual decisions (19.0.1.20.0): **Accept Order** and **Reject Order** appear on
  the backend POS order form only for incoming Blend orders (identified by their
  `blend_partner_reference`), with status `pending`, in draft, without any payment,
  and with Blend enabled. Normal POS orders and outgoing orders with only a
  `blend_order_id` never qualify. POS users/system administrators also need normal
  order write access in the selected company; the same checks run on RPC actions.
- Reject opens a confirmation wizard with a mandatory local reason (up to 1000
  characters). It cancels the unpaid POS order and queues `REJECTED`, not an extra
  `CANCELED` event. Accept queues `ACCEPTED` without payment. Both require the
  unified webhook, or the legacy POS schema plus HTTPS status webhook; subsequent cancellation
  of an already accepted order is not handled by these pending-order buttons.
- Order-row locking and a recheck at confirmation prevent conflicting manual
  decisions. Decision, audit metadata and outbound job are transactional. Chatter
  identifies the employee/time, escapes the reason, and no reason is added to the
  Blend wire contract. Repeating the same decision is a no-op; a contradictory
  decision is rejected. The old status selector routes accept/reject through the
  same workflow instead of only sending an inconsistent notification.
- The **Incoming Blend Order** section shows local status and decision-delivery
  state separately. Refresh to see the latest queue result. A pending/retry/failed
  job is not a confirmation from Blend. POS users see the delivery state only,
  not the admin-only integration payloads or tokens. Auto acceptance records its
  source as Automatic and uses the same delivery summary.

- Optional automatic acceptance (19.0.1.19.0): enable **Auto Accept Blend Orders**
  separately for each POS in the Blend Integration tab (system administrators only).
  It defaults to off. Enabled branches require a Brand ID and the unified
  webhook, or the legacy **POS API (order.updated)** schema plus a valid HTTPS
  **Blend Order Status URL**.
  Obtain the real webhook URL and any required outbound Bearer token from Blend;
  URL syntax validation cannot verify that an endpoint is working.
- Only newly created inbound `PENDING` orders are automatically marked `accepted`.
  Existing orders and other incoming statuses are not changed. The Odoo order
  remains a draft with no payment. Failed validation never queues acceptance.
- The `order.updated` / `ACCEPTED` event is queued atomically with the order,
  then sent by the outbound cron after commit. Ensure the cron is running and
  monitor pending/retry/failed logs. Local acceptance does not confirm delivery
  to Blend until the outbound log succeeds. Create replays and later payment
  callbacks reuse the same acceptance queue entry; network retries may still
  deliver the event more than once, so Blend must process retries idempotently.

- Create Order totals (19.0.1.17.0): `products[].total` excludes modifiers.
  Add every product total and each nested `modifiers[].total` exactly once to
  obtain the order `total`; these are extended line totals, not unit prices.
  Example: `50 + 12 + 24 + 23 = 109`. Modifier totals may exceed the base
  product total. The existing implied discount (`subtotal + tax - total`)
  is recorded without subtracting it again from the supplied final line totals.

- Give Blend the public outbound IP address(es) for its whitelist.
- Give Blend the HTTPS URLs displayed on the POS configuration: Login, Webhook,
  Get Menu, Get Branches, Create Order, and Update Order.
- In Postman, set only `odoo_domain`; never send a database name or database
  selection header to an Odoo.sh/production domain.
- Run the Postman collection with Sandbox URLs and secrets in Collection Variables.
- Verify login, Bearer rejection, menu, availability, branch pause/resume, order create,
  both status directions, retry behavior, idempotency, and Integration Logs.
- Verify `item.updated.is_stock` and `store.updated.status` are JSON booleans and
  that menu/category/product/modifier IDs returned by `GET /api/menu` are numbers.
- Rotate all temporary Sandbox/local passwords and tokens before go-live.

## Branch stock availability (19.0.1.16.0)

- `GET /api/menu` now combines manual/scheduled availability with stock for
  **Track Inventory** products (`is_storable`). The quantity used is on hand
  minus reserved, in the POS operation type's **Source Location** and its
  internal sublocations, restricted to that branch's company. Zero or negative
  stock returns `available_branches[].is_stock: false`. Positive stock does not
  override a manually disabled product/branch or its schedule.
- Untracked products/services keep their existing availability behavior. The
  current template-based menu/order contract uses `product_variant_id`; stock
  of a different variant does not make that orderable variant available. This
  is not recipe/BOM ingredient availability or an order-quantity reservation.
- Quant creation, updates, reservations/releases, inventory adjustments,
  transfers, returns and deletion create transactional availability checks in
  the existing outbound queue. The worker rechecks the final committed balance
  and sends **`item.updated` with Boolean `is_stock`** if a unified webhook is
  configured (19.0.1.23.0). Legacy routing uses `menu.updated` and requires a
  fresh menu pull. Modifier stock changes invalidate parent menu products with
  `menu.updated` in both modes. No numeric stock field is sent.
- Checks are persisted even for changes that individually remain positive, to
  avoid missing concurrent depletion across different stock quants. These
  checks also compare the last delivered state per branch/menu item; workers
  lock that state before sending, so a stale writer snapshot or another parent
  of the same modifier cannot suppress a needed update. Unchanged
  checks finish locally with `response_payload.skipped = true`, no HTTP attempt.
  Failed/ambiguous HTTP deliveries use the existing retry queue and continue to
  reconcile availability even if the stock has changed again. Unified item
  retries send the freshly calculated Boolean. Delivery is at least once.
  Rolled-back stock changes cannot
  produce a committed notification.
- Enable cron workers (`max_cron_threads >= 1`) and configure the HTTPS Blend
  unified webhook URL/token (or legacy Menu Event URL). Delivery happens after commit when the queue runs (not
  synchronously inside a stock write); outages can delay it. If POS stock moves
  are deferred until session closing, notifications also wait for the actual
  stock update. Time-only expiry/schedule changes are not stock mutations.
- Verify each POS Source Location/company mapping before enabling this version;
  a missing/invalid source never falls back to another warehouse's stock.
  Resync the menu after upgrade and after changing stock-location mappings.

