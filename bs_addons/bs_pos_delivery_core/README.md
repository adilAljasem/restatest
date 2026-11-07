# BS POS Delivery Core

Shared, provider-neutral delivery framework for Odoo 19 Point of Sale.

## Core v1 delivery flow

Core v1 validates normalized orders, creates one canonical delivery order per
connection/external ID, resolves configured products and payments, creates the
POS order on local acceptance when a session is open, and applies authenticated provider state updates.
Collected allocations post one POS payment per external reference. Expected
allocations stay visible without posting until collection is confirmed. POS
completion waits for full payment; platform-collected money requires review
before cancellation because a POS refund cannot return it through the platform.

The existing connector-owned paths continue working. Deliverect and Feedus
must be migrated and tested separately before they call the Core v1 services.

- [Core architecture and v1 contract](docs/CORE_CONTRACT_V1.md)
- [Developer guide and team workflow](docs/WORKFLOW.md)

## Dependency rule

Dependencies flow in one direction only:

```text
connector -> bs_pos_delivery_core
```

Core must never import a provider addon or contain a provider name. Each
connector registers its catalog metadata with `bs.delivery.provider.register_provider`
and implements `bs.delivery.adapter.<code>`.

Connectors authenticate incoming requests and use their configured provider credentials
for outgoing API calls through the shared HTTP client.

## Adapter contract

Adapters inherit `bs.delivery.adapter`, declare supported capabilities, and are
invoked through `bs.delivery.connection._dispatch`. Provider HTTP calls should
use `bs.delivery.http.client` so logging, masking, timing, and network error
mapping remain consistent.

## Reliability and data policy

- Incoming events are authenticated before being durably queued.
- `(connection_id, idempotency_key)` prevents duplicate processing.
- Webhook retries use bounded exponential backoff with jitter, and interrupted
  `processing` events are recovered automatically.
- Provider-facing order mutations can remain synchronous or use the optional
  durable outgoing queue with retries and stale-worker recovery.
- Correlation IDs connect webhook/outbox work with its sanitized logs.
- Raw bodies, completed events, and synchronization logs use configurable
  per-connection retention periods.
- Secrets may be supplied by connector-specific environment variables; database
  fields remain available for backwards compatibility and local installations.

Queued outgoing mode is enabled per connection. Synchronous mode remains the
default so existing connectors keep their current behavior after upgrade.

Outgoing mutations are serialized per order binding. A delayed or failed job
blocks later mutations for that order until it succeeds or an administrator
cancels the job; other orders remain eligible for processing.

## POS financial validation

The canonical POS import validates the proposed lines with Odoo's tax engine,
including the store fiscal position and currency rounding, before creating a
POS order or payment. Provider totals and tax must match that calculation.
Operational fee amounts are treated as gross amounts and retain the configured
product taxes. Differences are no longer hidden in an untaxed adjustment line.
Failed creation attempts roll back their POS records and binding links together.

This validation covers the canonical Core import. Legacy connector-owned POS
creation paths still need their own migration and verification. Remote acceptance
confirmation is separate from local acceptance and still needs connector-level
verification.

## POS completion and cancellation

Local/manual and automatic acceptance validate POS creation before recording the
transition when a POS session is open. If the mapped POS session is closed,
Core validates the order and configuration, records `Awaiting POS Session`, and
queues the unchanged provider acceptance operation immediately. No POS order,
payment, or invoice is created while waiting. The local acceptance deadline is
60 seconds after webhook arrival; an unconfirmed acceptance that reaches the
deadline becomes `Needs Review` and is not sent late. Manual and reject policies
retain their existing behavior.

Opening the mapped POS session triggers settlement; a one-minute cron checks for
missed triggers. Settlement requires a confirmed provider acceptance, reuses the
existing POS/payment/invoice path, and requests kitchen printing after the POS
order exists. Cancelled or rejected orders never settle. Managers see a warning
activity and can filter the order list by `Awaiting POS Session`; the activity
closes after settlement or cancellation. Provider acceptance remains authoritative;
accounting failure is recorded as `Needs Review` without discarding the order.

Delivery completion uses the native paid-order workflow, including stock moves.
An unpaid order keeps its external delivery state and exposes the POS blocker.
Completion/refunds wait for review when the POS session is closing or closed.
Cancellation removes unposted draft payments only after checking for accounting
documents, terminal references and linked online payments. Captured/external
payments require review; this module does not issue payment-gateway refunds.

Paid orders with ordinary POS payments receive a native refund once, using the
original payment methods. Existing draft refunds require review. A delivery
manager can retry blocked processing from the order form after correcting the
cause. Repeated processing does not create duplicate payments, pickings or
refunds. The same lifecycle does not yet cover legacy connector-owned flows.

## POS operations hub

The Core addon owns one provider-neutral OWL hub inside the Odoo 19 POS. It
shows a badge for every provider routed to the current POS and opens a live
five-stage board: Received, Accepted, Preparing, Ready, and Picked up.

- Changes arrive over the private `pos.config` bus channel, with polling as a
  recovery fallback.
- New received orders use a persistent notification and an optional sound
  alert.
- Cashiers can accept, reject with a configured reason, start preparation,
  mark ready, mark picked up, and mark delivered without leaving the POS.
- Order cards expose operational details such as masked customer phone,
  address, fulfillment type, driver contact, ETA, and scheduled time.
- The hub blocks transition actions while the browser is offline and resumes
  refreshes after connectivity returns.
- Kitchen auto-print jobs use claim/acknowledge leases to avoid duplicate
  printing across multiple open tills.
- The browser receives only the minimum board projection. Provider secrets,
  OAuth tokens, webhook secrets, and passwords remain server-side.

Providers and connectors do not ship their own competing POS boards. They
register capabilities and routes in Core, so installing or uninstalling a
connector changes the available badges without leaving provider-specific UI
artifacts behind.

## Adding a connector

1. Create `bs_pos_delivery_<code>` depending on `bs_pos_delivery_core`.
2. Call `register_provider` from the connector data file.
3. Implement `bs.delivery.adapter.<code>` and expose only supported capabilities.
4. Keep credentials, mappings, API clients, views, and provider workflows inside
   the connector module.
5. Use the generic webhook endpoint and order/product binding models where the
   external API supports them.
6. Authenticate every public ingress, validate branch/company scope, and keep
   credentials and network logging server-side.

Connector placeholders whose official contracts are not yet available may depend
directly on Core.  They must stay in `awaiting_api`, declare no capabilities, and
must not expose synthetic live endpoints.
