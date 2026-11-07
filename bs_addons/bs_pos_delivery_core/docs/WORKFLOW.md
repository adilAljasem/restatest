# Team Workflow & Engineering Guide

This guide explains how human developers and AI assistants collaborate on **Connect Core** (`bs_pos_delivery_core`) and external aggregator connectors (**Feedus**, **Blend**, **Deliverect**, **Hungerstation**, etc.).

---

## 1. The Core Golden Rule

```
┌─────────────────────────────────────────────────────────────┐
│                 CONNECTORS TRANSLATE                        │
│                 CORE EXECUTES                               │
└─────────────────────────────────────────────────────────────┘
```

* **Connectors only:**
  1. Authenticate incoming aggregator webhooks.
  2. Translate external JSON payloads into the standard **Core Contract v1** format, including normalizing provider-specific pricing and taxes (e.g. calculating net price and VAT if the platform only provides gross prices).
  3. Pass normalized payloads strictly to Core services (`_receive_order`, `_apply_provider_update`).
  4. Implement an outbound adapter class (`bs.delivery.adapter.<provider>`) for outgoing API calls.

* **Core owns everything else:**
  * Canonical order creation, validation, and deduplication (replay/idempotency protection).
  * Product and modifier/combo mapping.
  * Odoo tax engine calculation, fiscal positions, discounts, and currency rounding.
  * Payment method resolution and POS payment posting (`collected_by`).
  * POS order creation, customer assignment, and invoice generation.
  * Inbound webhook queues and outbound mutation outbox queues.
  * The unified POS Live Hub UI, sound alerts, and kitchen routing.

> **CRITICAL RULE: CONNECTORS MUST NEVER SKIP CORE**
> Connectors must **never** bypass Core by calling `pos.order.create()`, posting `pos.payment`, creating `account.move` invoices, or altering POS screens directly.
> 
> **Why skipping Core breaks the system:**
> * **Duplicate Orders & Payments:** Aggregators retry webhooks on timeouts. Skipping Core bypasses idempotent deduplication, causing duplicate tickets and double-charged payments.
> * **Broken Taxes & Z-Reports:** Core uses `bs.delivery.money.engine` to align delivery orders with Odoo's fiscal positions. Bypassing Core leads to mismatched taxes and failed accounting audits.
> * **Broken Staff Operations:** Core handles the Live Hub feed, sound alerts, kitchen printing, and status synchronizations. Orders created directly in POS will be invisible to kitchen staff or delivery drivers.
> * **Payment Mismanagement:** Core cleanly isolates aggregator escrow payments from in-store cash payments. Bypassing Core corrupts the physical cash drawer tally.

---

## 2. Developing or Migrating a Connector in 4 Steps

When adding a new delivery aggregator or migrating an existing one:

### Step 1: Declare Dependencies
In the connector's `__manifest__.py`, depend **only** on `bs_pos_delivery_core` (and any model-specific Odoo modules like `pos_restaurant` if using combo templates):
```python
"depends": [
    "bs_pos_delivery_core",
]
```

### Step 2: Register the Provider & Adapter
In your connector module:
1. Register the provider in XML (`bs.delivery.provider`).
2. Subclass `bs.delivery.adapter` in Python to handle outgoing API requests:
```python
class FeedusAdapter(models.AbstractModel):
    _name = "bs.delivery.adapter.feedus"
    _inherit = "bs.delivery.adapter"

    def accept_order(self, connection, store, external_order_id, **kwargs):
        # Make outgoing HTTP call to provider API
        ...
```

### Step 3: Implement Webhook Ingestion, Tax Normalization & Translation
In your controller:
1. Authenticate the incoming HTTP request.
2. Store the raw event in `bs.delivery.webhook.event`.
3. Normalize pricing and taxes into `contract_version: 1`:
   - If the aggregator sends net prices + VAT (e.g. Deliverect), map them directly.
   - If the aggregator sends gross/tax-inclusive prices (e.g. FeedUs), compute the tax-excluded net and VAT using the mapped product's tax rate before passing to Core.
4. Delegate execution strictly to Core:
```python
order = request.env["bs.delivery.order.service"]._receive_order(
    connection=connection,
    store=store_binding,
    payload=normalized_payload,
    event=webhook_event,
)
```

### Step 4: Add Automated Acceptance Tests
Write tests verifying that incoming payloads create valid Core orders and map payments properly without directly manipulating POS tables.

---

## 3. Git & Branching Strategy

All team development targets the **`STG`** integration branch.

```
                  ┌── feature/CON-xx-my-task ──┐
                  │   (Develop & Test)         │
                  ▼                            │
STG ──────────────┴────────────────────────────┴──► Merge PR into STG
```

### Working on a Task:
1. **Pull the latest `STG` branch:**
   ```powershell
   git checkout STG
   git pull origin STG
   ```
2. **Create your task branch:**
   ```powershell
   git checkout -b feature/CON-XX-short-description
   ```
3. **Commit clean changes:**
   * Stage only task-related files.
   * Never commit `__pycache__` or `.pyc` files.
4. **Pull latest `STG` before pushing:**
   ```powershell
   git pull --no-rebase origin STG
   ```
5. **Run the test suite:**
   ```powershell
   python odoo19/odoo-bin -c odoo.conf -d <db_name> --test-enable --stop-after-init --test-tags /bs_pos_delivery_core
   ```
6. **Open a Pull Request into `STG`.**

---

## 4. Jira Task Lifecycle

| Status | Meaning |
| :--- | :--- |
| **To Do** | Work has not started. |
| **In Progress** | Assigned engineer is actively developing and testing on their branch. |
| **In Review** | Pull request is open against `STG` with all tests passing. Peer review in progress. |
| **Done** | PR is merged into `STG` and verified on the staging database. |

### Handoffs:
If work transfers between engineers or AI agents:
* Reassign the Jira ticket.
* Ensure the branch is pushed to remote so the next engineer can seamlessly continue.
* Do not leave unstaged or uncommitted changes on local machines.

---

## 5. Technical References

* **[Core Contract v1 Specification](CORE_CONTRACT_V1.md)** — Deep technical specification, exact JSON payload schema, service signatures, and error codes for developers and AI agents.
* **[Connect Core Readme](../README.md)** — Architectural summary and dependency boundaries.

