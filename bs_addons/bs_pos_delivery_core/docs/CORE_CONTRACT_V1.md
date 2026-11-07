# Core Contract v1 Specification

**Technical Blueprint for Software Engineers and AI Agents**

This document specifies the exact contract, data models, service interfaces, and mathematical rules governing **Connect Core** (`bs_pos_delivery_core`) for Odoo 19 Point of Sale.

---

## 1. Architectural Invariant: "Connectors Translate, Core Executes"

Core gives Odoo POS one consistent, robust way to handle delivery orders regardless of the aggregator (Feedus, Blend, Deliverect, Jahez, Hungerstation, etc.).

```
Incoming: Aggregator ──► Connector Controller ──► Core Webhook Queue ──► Core Services ──► Odoo POS & Accounting
Outgoing: POS Action ──► Core Workflow        ──► Core Outbox Queue  ──► Connector Adapter ──► Aggregator API
```

### Absolute Boundary Rules:
1. **One-Way Dependency:** `connector -> bs_pos_delivery_core`. Core never imports or names a connector.
2. **Never Bypass Core (Strict Anti-Bypass Invariant):** Aggregator modules and connectors must **NEVER** bypass Core to interact with POS directly. Connectors must **never**:
   * Execute `self.env['pos.order'].create(...)` or write to POS orders.
   * Execute `self.env['pos.payment'].create(...)` or post payments directly.
   * Directly create invoices (`account.move`) or journal entries for delivery orders.
   * Directly manipulate POS kitchen preparation queues or printer dispatchers.
   * Modify or override POS frontend screens/OWL templates directly.
   
   **Why skipping Core is strictly prohibited:**
   * **Idempotency & Replay Safety:** Direct creation bypasses webhook deduplication, leading to duplicate POS orders and double payments when aggregators retry.
   * **Tax & Accounting Integrity:** Bypassing Core skips `bs.delivery.money.engine` and Odoo fiscal positions, producing inconsistent VAT lines, breaking Z-reports, and corrupting audit logs.
   * **POS Experience & UI Synchronization:** Core orchestrates real-time Live Hub feeds, audio alarms, order accept/reject actions, and kitchen routing. Direct records will fail to alert staff or show on screens.
   * **Payment Reconciliation:** Core enforces `collected_by` separation (restaurant vs. aggregator escrow). Direct payments corrupt the cashier's physical cash drawer and bank statements.
   * **Outbox Mutation Reliability:** Status changes must route through Core's durable outbox queue to guarantee upstream sync with aggregator APIs.

3. **Connector Scope:** A connector's sole responsibility is to:
   * Authenticate provider webhook requests and verify signatures.
   * Handle provider-specific tax/pricing quirks and normalize them into `contract_version: 1`.
   * Call Core service methods (`_receive_order`, `_apply_provider_update`).
   * Subclasses `bs.delivery.adapter` to execute outgoing HTTP requests dispatched by Core.

---

## 2. Service Interfaces & Method Signatures

All Core methods below are internal Python API methods located in `bs_pos_delivery_core`. They validate security, tenant company scope, store bindings, and event records.

| Service | Method Signature | Returns | Behavior & Replay Policy |
| :--- | :--- | :--- | :--- |
| **`bs.delivery.order.service`** | `_receive_order(connection, store, payload, *, event)` | `bs.delivery.order` | Atomically validates, normalizes, and creates delivery order. **Exact replay** returns existing order. Changed snapshot raises non-retryable conflict error. |
| **`bs.delivery.order.service`** | `_apply_provider_update(connection, store, external_order_id, payload, *, event)` | `bs.delivery.order` | Advances delivery status (`accepted`, `preparing`, `ready`, `delivered`, `cancelled`). Uses `occurred_at` to drop stale out-of-order updates. |
| **`bs.delivery.payment.service`** | `_resolve_payment_method(connection, store, *, external_code, collected_by, external_source="default")` | `pos.payment.method` | Pure lookup; performs **no database writes**. Matches explicit collector scope (`restaurant` vs `aggregator`). |
| **`bs.delivery.payment.service`** | `_record_payment(order, payload, *, event)` | `pos.payment` | Posts payment to linked POS order once collection is confirmed (`collected`). Exact replay returns existing payment without double-posting. |
| **`bs.delivery.contract`** | `_validate_order_payload(payload, currency=currency)` | `dict` (clean copy) | Validates JSON schema, mathematical reconciliation, and rounding. Raises `DeliveryBusinessError(code="validation_error")` on failure. |

---

## 3. Normalized Payload Specification (`contract_version: 1`)

### 3.1 Order Schema
```json
{
  "contract_version": 1,
  "external_order_id": "ORDER-1001",
  "external_source": "feedus",
  "currency": "SAR",
  "fulfillment_type": "delivery",
  "price_basis": "tax_excluded",
  "lines": [
    {
      "external_line_id": "line-1",
      "external_product_id": "SKU-BURGER-01",
      "name": "Classic Burger",
      "quantity": 2,
      "price_unit": 25.00,
      "discount_amount": 5.00,
      "vat_amount": 6.75,
      "price_subtotal": 45.00,
      "note": "No onions"
    },
    {
      "external_line_id": "line-2",
      "external_product_id": "SKU-EXTRA-CHEESE",
      "name": "Extra Cheddar",
      "quantity": 2,
      "price_unit": 3.00,
      "discount_amount": 0.00,
      "vat_amount": 0.90,
      "price_subtotal": 6.00,
      "parent_external_line_id": "line-1"
    }
  ],
  "fees": [
    {
      "kind": "delivery",
      "amount": 10.00,
      "vat_amount": 1.50
    }
  ],
  "totals": {
    "items_subtotal": 56.00,
    "discount_amount": 5.00,
    "fee_amount": 10.00,
    "vat_amount": 9.15,
    "rounding_amount": 0.00,
    "amount_total": 70.15
  },
  "payments": [
    {
      "external_payment_code": "visa",
      "external_source": "feedus",
      "amount": 70.15,
      "currency": "SAR",
      "collected_by": "aggregator",
      "collection_status": "collected",
      "external_payment_reference": "TXN-884920"
    }
  ],
  "customer": {
    "name": "Ahmed Salem",
    "phone": "+966500000000",
    "address": "Olaya St, Riyadh",
    "note": "Leave at front desk"
  }
}
```

### 3.2 Mathematical Reconciliation Invariants
All figures must be finite numbers conforming to currency decimal precision. Core enforces:

1. **Line Subtotal Formula:**
   $$\text{price\_subtotal} = \text{round}(\text{quantity} \times \text{price\_unit}) - \text{discount\_amount}$$
2. **Items Subtotal Formula:**
   $$\text{items\_subtotal} = \sum (\text{round}(\text{quantity} \times \text{price\_unit})) \quad \text{(all lines including charged modifiers)}$$
3. **Total Discount Formula:**
   $$\text{discount\_amount} = \sum \text{line.discount\_amount} + \sum \text{untaxed\_discounts.amount}$$
4. **Order Grand Total Formula:**
   $$\text{amount\_total} = \text{items\_subtotal} - \text{discount\_amount} + \text{fee\_amount} + \text{vat\_amount} + \text{rounding\_amount}$$

Optional ``untaxed_discounts`` contains positive ``{"label": str, "amount": number}``
components. They represent separate negative untaxed POS/invoice lines; they
do not reduce product tax bases or alter item line prices. Core validates each
amount against currency precision and reconciles the aggregate to ``totals``.
Wallet tender belongs in ``payments``, never in ``untaxed_discounts``.

### 3.3 Tax & Pricing Normalization Strategy

External aggregators vary dramatically in how they structure prices and taxes:
* **Tax-Aware Aggregators (e.g., Deliverect):** Provide explicit net prices alongside explicit item-level VAT amounts.
* **Tax-Blind / Gross Aggregators (e.g., FeedUs):** Provide consumer menu retail prices that are already tax-inclusive (gross), omitting separate VAT lines.
* **Aggregators with Platform Fees:** Send delivery fees, service surcharges, or driver tips where some fees are taxable at standard rate and others are tax-exempt.

#### Universal Invariant: Core Enforces `price_basis: "tax_excluded"`
Core strictly standardizes on a tax-excluded basis. Core **never** guesses or embeds aggregator-specific reverse-tax logic into its universal core models. The connector layer is responsible for translating provider pricing nuances into the standard contract.

#### Connector Normalization Rules:
1. **Tax-Aware Platforms:**
   * Map provider net unit price $\rightarrow$ `price_unit`.
   * Map provider VAT $\rightarrow$ `vat_amount`.
   * Map line subtotal $\rightarrow$ `price_subtotal`.

2. **Tax-Blind / Gross Platforms (e.g., FeedUs):**
   * The connector resolves the mapped Odoo `product.product` (or the store's default tax configuration).
   * The connector calculates the tax-excluded net and VAT before calling Core:
     $$\text{price\_unit} = \text{round}\left(\frac{\text{gross\_unit}}{1 + \text{tax\_rate}}, \text{decimal\_places}\right)$$
     $$\text{price\_subtotal} = \text{round}(\text{quantity} \times \text{price\_unit}) - \text{discount\_amount}$$
     $$\text{vat\_amount} = \text{round}(\text{price\_subtotal} \times \text{tax\_rate}, \text{decimal\_places})$$
   * Pass the calculated figures with `price_basis: "tax_excluded"`.

3. **Core Fiscal Execution:**
   * Core validates that line subtotals, VAT, discounts, and order grand totals reconcile mathematically.
   * Core's `bs.delivery.money.engine` applies Odoo's native tax calculation to generate POS order lines, ensuring complete alignment with official Odoo fiscal positions, Z-reports, and tax declarations without rounding drift.

---

## 4. Modifier / Combo Hierarchy Rules

* A modifier references its parent item via `parent_external_line_id`.
* The parent line must exist within the same `lines` array.
* Nested modifiers (modifiers on modifiers) are prohibited in v1.
* **Included / Free Modifiers:** If a modifier is included in the base meal price, set `price_unit: 0`, `discount_amount: 0`, `vat_amount: 0`, and `price_subtotal: 0`.
* **Charged Modifiers:** Must declare their own `price_unit` and `price_subtotal`. The parent line's `price_unit` must not double-count the modifier price.

---

## 5. Payment Allocation & Collector Scopes

Every payment entry in `payments` must declare explicit collection facts:

* **`collected_by`:**
  * `"aggregator"`: Customer paid online in advance (prepaid). Aggregator holds the funds.
  * `"restaurant"`: Driver/customer will pay the store upon handover (Cash on delivery / Card at door).
  * `"unknown"`: Ambiguous; prohibited if `collection_status == 'collected'`.
* **`collection_status`:**
  * `"collected"`: Confirmed captured money. Requires non-empty `external_payment_reference` and known `collected_by`. Core posts a `pos.payment`.
  * `"expected"`: Promised future collection (e.g. cash on delivery). Visible on the delivery order payments tab, but does **not** create a `pos.payment` until collection is confirmed.

### Resolver Priority Hierarchy:
1. Exact `(source, code)` configured on the **POS Branch** (`pos.config`).
2. Exact `(source, code)` configured on the **Delivery Connection** (`bs.delivery.connection`).
3. Default `("default", code)` configured on the **POS Branch**.
4. Default `("default", code)` configured on the **Delivery Connection**.

---

## 6. Delivery States vs. POS Processing States

Core deliberately isolates operational delivery statuses from POS accounting statuses:

### Delivery Operational States (`state` on `bs.delivery.order`):
* `received` $\rightarrow$ `accepted` $\rightarrow$ `preparing` $\rightarrow$ `ready` $\rightarrow$ `picked_up` $\rightarrow$ `delivered` (terminal)
* Rejections/cancellations: `rejected`, `cancelled` (terminal)

### POS Processing States (`pos_processing_state`):
* `pending`: Delivery order received; POS order not yet created.
* `ready`: POS order successfully created with reconciled lines and taxes.
* `done`: POS order finalized and fully paid.
* `blocked`: Failed financial validation or closed POS session; requires operator review.

---

## 7. Standard Connector Implementation Template

Here is how a connector (e.g. `bs_pos_delivery_feedus`) implements the Core contract:

```python
# controllers/feedus_webhook.py
from odoo import http
from odoo.http import request

class FeedusWebhookController(http.Controller):

    @http.route("/api/feedus/v1/orders", type="json", auth="none", methods=["POST"])
    def receive_order(self):
        # 1. Authenticate connection
        connection = self._authenticate_feedus_request()
        store_binding = self._resolve_store_binding(connection)

        # 2. Ingest raw webhook event (durable queue)
        event = request.env["bs.delivery.webhook.event"].sudo().create({
            "connection_id": connection.id,
            "external_event_id": request.httprequest.headers.get("X-Feedus-Delivery-ID"),
            "event_type": "order.created",
            "raw_payload": request.jsonrequest,
        })

        # 3. Translate provider payload to Core Contract v1
        normalized_payload = self._translate_feedus_to_contract_v1(request.jsonrequest)

        # 4. Delegate execution to Core
        order = request.env["bs.delivery.order.service"].sudo()._receive_order(
            connection=connection,
            store=store_binding,
            payload=normalized_payload,
            event=event,
        )

        return {"status": "ok", "delivery_order_id": order.id}
```

---

## 8. Verification & Test Suite

All changes to Core must pass the complete test suite:
```powershell
python odoo19/odoo-bin -c odoo.conf -d <test_db> --test-enable --stop-after-init --test-tags /bs_pos_delivery_core
```

* `TestCoreV1Flow` (22 tests): End-to-end acceptance, POS creation, modifiers, fees, invoicing, and rollback.
* `TestDeliveryPayloadContract` (20 tests): Payload schema validation, mathematical constraints, and precision.
* `TestDeliveryPaymentResolver` (6 tests): Collector scope validation and method resolution.

See [WORKFLOW.md](WORKFLOW.md) for branch strategy, PR reviews, and team handoffs.
