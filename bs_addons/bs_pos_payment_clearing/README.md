# POS Payment Clearing

Odoo 19 addon: `bs_pos_payment_clearing`.

## Configuration and accounting

- For cash and bank payment methods with **Identify Customer** enabled, select
  a **Customer** on the payment method. Adding that method to a POS order selects
  its customer for the whole order. If multiple identified payment methods are
  added, the last one added determines the order customer; methods without
  Identify Customer leave the customer unchanged. Odoo then applies that
  customer's pricelist and fiscal position, and recalculates the new payment
  line amount. Existing identified cash/bank methods need a customer assigned
  to use this automatic selection. Odoo's
  **Customer Account** method may remain without a fixed customer so its native
  per-order customer flow continues.
- Cash and Bank payment methods require an active, reconcilable Current Asset
  Outstanding Account, different from their liquidity, suspense and receivable
  accounts. Native creation of payment methods proposes the chart's default
  outstanding account; explicitly clearing the field is rejected.
- The destination journal is the **Point of Sale Journal** in POS settings
  (`res.config.settings.pos_journal_id` / `pos.config.journal_id`). It may be a
  Miscellaneous or Sales journal. Each new session captures this journal.
- Cash collections debit Outstanding and credit POS receivable (or customer
  receivable for identified payments). Refunds reverse the signs.
- New sessions combine cash and bank **closing collections** in one posted entry
  in the captured POS journal. The sales entry is unchanged. Each collection
  component retains separate Outstanding/receivable items and a separate draft
  settlement, including when several methods share the same account.
- From version **19.0.1.3.0**, new sessions include the closing cash shortage or
  surplus in this collection entry. The cash source item is the net payment
  amount plus the signed count difference; customer receivables retain the gross
  payments. The standard separate cash-count difference entry is suppressed.
  Collection Details shows Payments, Closing Difference and the net Amount.
  Identified cash customers keep separate receivable items but share one cash
  component/transfer per payment method. A zero net amount creates no transfer.
- To use a physical drawer instead of cash clearing, set **Branch Cash Account**
  on the cash payment method before opening a new session. The cash journal's
  default account is the **POS drawer**; Branch Cash Account must be a separate,
  active Cash account in the same company. The posted collection debits the
  drawer and its draft transfer debits branch cash/credits the drawer. These
  accounts are captured on each component so later payment-method configuration
  changes do not reroute its transfer. Cash-account matching uses Odoo's native
  liquidity reconciliation. Leaving this field empty retains cash clearing.
  Bank collections still debit Outstanding and settle into the bank journal's
  default account. Outstanding remains required for legacy and ancillary cash
  movements; configuring branch cash does not reclassify historical balances.
- Example: opening float 50, cash sales 150, card sales 70, counted cash 190.
  The POS collection posts drawer debit 140, loss debit 10, cash receivable
  credit 150, bank Outstanding debit 70 and card receivable credit 70. It
  creates draft cash and bank transfers of 140 and 70. Posting the cash transfer
  leaves the opening float 50 in the drawer (assuming the opening balance is
  already accounted for). This is a transfer of the net collection, not a full
  sweep of counted cash, and should only be posted when that amount is handed over.
- POS order/payment records remain standard. Grouped collections use the
  read-only `bs.pos.collection.component` model instead of manufacturing native
  bank payments or cash statement records sharing a move: Odoo's native payment
  and statement synchronization assumes separate parent moves. View these
  components from the session's **Collection Details** button.
- Cash In/Out and cash-management handovers remain separate
  native cash movements with their own entries and drafts. They are not merged
  into the closing collection. Cash count differences on older sessions also
  retain separate entries. Existing-session bank payments retain the legacy
  individual-entry flow. Opening cash, cash counts and POS payment lists remain
  unchanged.
- Each source collection component/cash movement creates one **Draft** settlement
  in the original payment-method journal. It debits that journal's default
  cash/bank account and credits Outstanding, using the signed source amount;
  refunds, cash-out and negative differences reverse those sides. This includes
  cash in/out, legacy count differences and the optional cash-management handover.
  Drafts do not affect posted balances and are not automatically posted.
- Posting the draft automatically reconciles its Outstanding item with its
  exact source item, never with other sessions/payments sharing that account.
  The original remains posted in the POS settings journal. Bank and cash
  settlement drafts follow source corrections until their first posting.
  Once posted, their journal items and amounts remain independent of later POS
  source corrections, including after reset to draft; any unmatched Outstanding
  balance remains open.
  Finalized grouped collection items are immutable;
  use standard reversal entries for corrections. Resetting a settlement to
  draft releases only that settlement's reconciliation.
  Combined collections support **Reset to Draft** and reposting after all linked
  posted settlements have been reset to draft. Standard accounting locks and
  access rights still apply; session/component links and generated amounts are
  preserved, and reposting reuses the existing settlements.
  Cancelled settlements are retained and are not recreated; reset the same
  entry to draft if needed. Use Cancel instead of deleting automatic drafts.
- The source entry has a **POS Settlements** button; each draft has **Source POS
  Payment Entry** and **Source Collection Item** links. Source-line uniqueness
  prevents duplicate drafts without merging different payment components.
  The session Journal Items action includes the grouped entry and its drafts
  (remove the standard Posted filter to see drafts).
- Bank statement importing/synchronization is deliberately unchanged. Posting
  a settlement already recognizes Cash/Bank; importing the same receipt as an
  independent bank entry can double-count it. This workflow does not deduplicate
  bank imports. A source already partially/fully matched elsewhere cannot have
  its full settlement posted until that other match is resolved.
- If `pos_cash_management` is installed and its transfer option is enabled, its
  existing counted-cash handover credits Outstanding instead of Cash, with its
  original suspense counterpart. This is still a cash-out/handover: it does not
  create a new bank deposit automatically. Its additional settlement is a draft
  credit to the original cash journal's default account, not a bank deposit.
  Existing count/blind-count and cashier-partner behavior is retained.
  Optional `pos_analytic_account`
  distributions are applied to routed expense/income lines.

## Rollout and history

Only sessions **created after installation** use clearing. Already-open sessions
finish with the previous accounting flow. Grouping is captured separately for
sessions created after the grouping upgrade (19.0.1.2.0); older sessions keep
individual collection entries. No historical entries or payment
methods are rewritten. Settlement routing is captured on newly created payment
entries; the upgrade does not backfill old posted entries. After closing old
sessions, fill missing Outstanding Accounts before opening new sessions.
Net cash-count handling is captured on sessions created after 19.0.1.3.0;
sessions already open at upgrade retain their previous count-entry behavior.
The POS journal cannot be changed while a
session is open; persisted clearing routing cannot be reassigned.

An opening drawer balance is not a new collection: it is not posted again or
automatically reclassified by this addon. On rollout, any existing physical cash
balance carried in the old Cash ledger requires the usual accountant-controlled
opening/reclassification entry if it will now be tracked in Outstanding.

The addon intentionally preserves legacy/ancillary cash statement records in the POS journal.
Inspect them through the session's cash lines/journal items; they are not bank
statement imports. Do not uninstall the addon from a database with clearing
history without a reviewed accounting/data migration, because standard Odoo
alone does not interpret these statement liquidity accounts.

## Tests

`tests/test_payment_clearing.py` covers setup, cash/bank/mixed collections,
identified customers, invoices, refunds, change, cash movements/differences,
currencies, routing immutability, cashier rights, reconciliation, non-POS
isolation, duplicate closing, draft settlement creation/posting, exact-source
matching, resets, reversals, cancellation, source edits and duplicate protection.
The cash-management/analytic integration test runs when those optional addons
are installed.

`tests/test_grouped_collection.py` covers the single posted collection with
separate settlements, independent matching on shared accounts, split customers,
refunds, invoices, currency conversion, count/bank differences, cashier rights,
legacy snapshots, repeat close, source immutability and reversal handling.

`tests/test_net_cash_collection.py` covers the 150/70/10 example, drawer-to-branch
transfers, surpluses, zero net amounts, refunds, foreign currency, identified
customers, cash in/out, empty/card-only sessions, legacy sessions, account
configuration validation and captured routing after settings changes.

Local validation only; no deployment or production database changes.

Validation of 19.0.1.3.0 on `bs_pos_payment_clearing_test_20260915`:

- Static/XML/manifest checks and Ruff passed.
- Full module suite: 96 tests, 0 failures/errors; 3 optional cash-management
  integration tests skipped because that addon is not installed in this test DB.
- The exact 150 cash / 70 card / 10 shortage example passed, including draft
  transfers, posting, reconciliation and reset/repost without duplicate transfers.

Historical validation of 19.0.1.2.0 on the local Odoo 19 runtime with `account_accountant`,
`pos_cash_management` and `pos_analytic_account` installed:

- Static/XML/manifest checks and Ruff: no errors or warnings.
- Full legacy and grouped suites: 80 tests, 0 failures, 0 errors on the isolated
  local test database (60 legacy-session regressions + 20 grouping tests).
- Enterprise cash-deposit and card-settlement widget methods were exercised,
  including the matching-candidate domain and reconciliation of Outstanding.
- Physical cash drawers/payment terminals and live-browser/offline scenarios
  require the usual operational acceptance test; no frontend code is changed.
