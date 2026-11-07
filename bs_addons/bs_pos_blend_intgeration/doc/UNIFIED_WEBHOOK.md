# Unified outbound Blend webhook — 19.0.1.23.0

## Configuration

After upgrading the addon, open POS Configuration → Blend Integration →
Outbound Events (Odoo calls Blend). Set **Blend Outbound Webhook URL** on each
participating POS. The staging URL supplied for this integration is:

```text
https://webhook.api.staging-v2.tryblend.net/integration/webhook/pos/pino-sa
```

This is an example for the user's staging integration, not a default for other
brands or production. The field requires a complete HTTPS URL including its
path and a configured Brand ID. The manager dashboard URL is not a webhook.
Confirm Branch ID, Brand ID, and any required **Outbound Bearer Token** with
Blend. Dashboard credentials and the token returned by Odoo `/api/login` are
not automatically valid outbound credentials.

## Routing and payloads

All four events use POST with a complete JSON body and `Content-Type:
application/json`. They share the configured destination; the event selects
the operation in Blend. `brand_id` and UTC `event_time` are included in each.

| Event | Trigger / additional fields |
| --- | --- |
| `menu.updated` | Published menu product/price/image/category/modifier edits; `entity: {id, type}`. Blend fetches the updated menu. |
| `item.updated` | Effective availability changes for a published product in a branch; `branch_id`, `product_id`, Boolean `is_stock`. |
| `store.updated` | A local branch-availability change; `branch_id`, Boolean `status`. |
| `order.updated` | Existing supported order transitions, including manual/automatic acceptance and rejection; `branch_id`, `order_id`, status string. |

The unified URL selects the `order.updated` schema automatically, even if the
stored legacy schema is `primary`. Existing order ID and status mappings are
unchanged. No new order statuses or `modifier.updated` event are invented.
Modifier availability changes still invalidate parent products with
`menu.updated`, because the agreed item event identifies a base product.

**Legacy `order_created` remains separate**, using Blend Order Created URL.
As of 19.0.1.24.0 it also requires the administrator-only **Enable Legacy Order
Creation** switch, off by default (including existing configurations after
upgrade). The URL section is hidden while off; its stored value is preserved.
No new `order_created` jobs or payload builds occur while disabled. Old waiting
jobs are retained and marked Failed, with no network call or automatic retry,
when processed while disabled. Enable the switch and explicitly Reset and Retry
only if the legacy event is still required. This cannot recall an HTTP request
already in flight. The four unified events are unaffected.
The unified contract does not define that event. Without a unified URL,
existing menu/status URLs and schema behavior remain available. Odoo's inbound
login/menu/branches/orders/webhook endpoints are unchanged. Incoming
availability updates do not echo another outbound availability event.

The Postman collection includes all four requests under
**04 - Alternate POS Webhooks → B - Unified Outbound Webhook (Odoo to Blend)**.
Set `blend_outbound_webhook_url` explicitly; it is blank by default to avoid an
accidental send. Set `is_available` to `true` or `false`, without quotes in JSON.
Only run the relevant requests against an authorized test environment; sending
these events can change availability or order status at the receiver.

## Stock and queue behavior

- Stock uses on hand minus reserved for Track Inventory products, in the POS
  source location/internal descendants and the correct company. Zero/negative
  becomes unavailable; a positive restock still respects manual/schedule rules.
- Stock/manual availability and branch changes enqueue transactionally. HTTP
  runs after commit through the existing queue, not inside stock writes.
- The worker recomputes item/store availability immediately before each send,
  including retries. Last delivery is tracked per branch, event, destination,
  and target; unchanged checks may finish with `skipped: true` without HTTP.
- A timeout is ambiguous: Blend might have received the request. Such retries
  send the current state even if the local state has returned to its previous
  value. Manual Reset and Retry preserves this uncertainty despite resetting
  the attempt counter. Delivery is at least once, not exactly once.
- Changes can coalesce into the final state before processing. The queue must
  run and Blend must be reachable; this is not a zero-latency guarantee.
- For timely POS deductions, stock updates must be in real time, not deferred
  until session closing. Pending/accepted orders do not by themselves guarantee
  inventory reservation. This change adds no clock-only schedule-boundary job.

## Existing queued messages and ID safety

### Shared catalogue notifications (19.0.1.26.0)

One catalogue edit creates one `menu.updated` job per entity and shared delivery
scope, not per POS branch. The scope is the company, non-empty Brand ID, effective
menu webhook URL and outbound bearer credential. Different companies, brands,
URLs or outbound credentials remain separate. Blank brands also remain separate.
Each new save has its own change ID, even when two saves occur in the same second.
Product, category and modifier-group notifications retain their existing payload.

The queue record's **Shared Menu Scope** tab lists all affected POS configuration
IDs and a hashed destination fingerprint; credentials are never stored in this
metadata or sent in the JSON body. The record's Config is only the sending
representative. Old and new publication scopes are retained for moves, archives
and deletions. Product identity validation covers every included branch.

This grouping applies only to catalogue invalidations. `item.updated`,
`store.updated`, order events, and `menu.updated` jobs carrying branch-specific
stock checks (including modifier availability checks) are not merged. Branch
stock baselines, recomputation, reservations and retry behavior are unchanged.

Changing a participating branch's destination, authentication, company, brand,
or active/enabled state blocks that queued group instead of silently dropping
the branch. **Reset and Retry** can adopt a uniform new URL/credential for the
same company/brand and branches. Divergent settings must be reviewed; a queued
payload cannot be reassigned to another brand. Existing pre-upgrade jobs/history
are preserved, not retrospectively merged or deleted. Grouping prevents duplicate
branch jobs, but transport retries are still at-least-once delivery.

### Acceptance delivery protection (19.0.1.25.0)

An incoming `PREPARING` update does not echo `ACCEPTED`. Payment and POS sync
callbacks also do not enqueue acceptance after preparation, delivery or a
terminal status. Acceptance uses a stable per-order queue key, including
orders without an inbound partner reference.

Before sending `order.updated`, the worker locks the queue record and the
order, then rechecks their state. Old `ACCEPTED` jobs (including retries and
pre-upgrade duplicates) become **Superseded** when the order has advanced,
another acceptance was already delivered, or a newer outgoing status exists.
Their request, previous response and attempt history are retained. Superseded
is not a successful delivery and cannot be reset/replayed. The same checks
apply to **Process Now** and cron. Successful jobs cannot be sent again;
failed jobs require **Reset and Retry**. Busy records are left for a later run.

If acceptance is still current, timeouts remain retryable: the receiver may
have received a request whose response was lost. This is at-least-once
delivery, not an exactly-once guarantee; receiver-side deduplication is still
needed. No new required Blend payload fields or headers were introduced.

Changing a URL does not silently rewrite old queue entries. A pending supported
event with a different destination is blocked with a clear error. Review it,
then use **Reset and Retry** to adopt the current unified URL. The action only
accepts waiting/failed outbound logs, never successful or processing entries.
Brand/branch changes are not silently rewritten. Old `order_updated` payloads
are not automatically converted to the new contract; review them separately.

Published products must have unique wire IDs within each branch. Native template
IDs can otherwise collide with imported `blend_external_id` values. Menu/order
resolution and product-event delivery now reject ambiguous IDs instead of
choosing another product's stock. Numeric spellings such as `123` and `00123`
are treated as the same ID. No live IDs are automatically renumbered: coordinate
correction/resynchronization with Blend if a real catalogue has a collision.
Inventory writes still commit; an ambiguous notification is logged for retry.

## Local verification and rollout boundary

Automated tests use the explicit local test database and mocked outbound HTTP.
They verify event routing, complete payloads, Boolean types, stock depletion /
restock / reservations, retries, branch/manual changes, no inbound echo,
accept/reject/auto-accept, URL changes, view structure, and ID ambiguity.
The full module suite also covers existing HTTP endpoints, images, stock
lifecycle, company scoping, and legacy behavior.

No staging/production configuration or data was changed and no real Blend
webhook was called. After authorized deployment: configure the URL per branch,
resync the menu, verify the active queue cron/workers, review old pending jobs,
and confirm actual receipt/processing with Blend. Monitor Failed/Retry logs;
retries stop at the configured attempt limit.
