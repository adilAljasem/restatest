# Exclusive Customer Discounts

Odoo 19 add-on depending on `pos_loyalty`. Install it and reload POS sessions.

## Configuration

On a Promotions program, enable Exclusive Customer and use Customer Domain to
select partners. Enable Limit for Customer to choose Maximum Uses and Period.
An empty domain includes all customers; a disabled customer limit is unlimited.

Weeks follow Start Date in seven-day blocks, otherwise Saturday through Friday.
Day, month and year use calendar boundaries. Business dates use the company
partner timezone, with UTC fallback. The authoritative usage date is the server
payment date, preventing backdated client orders from selecting an older quota.

POS requires online eligibility checks and cashier consent. Server validation
runs before paid state changes. Ordered program row locks and a row update make
competing repeatable-read transactions retry rather than accept a stale quota.
Native reward calculations are reused. Usage rows are unique by program,
customer and order. Cancellation removes usage.

The native report shows tax-inclusive totals. Discount Percentage is the actual
effective percentage of this promotion against the order total before it,
which may differ from the configured percentage for product-specific rewards.

## Validation

- `node --test exclusive_customer_discount/tests/confirmation.test.cjs`: 13 passed.
- `python exclusive_customer_discount/tests/test_periods_standalone.py`: calendar,
  leap-year, Saturday fallback and anchored-week boundary cases passed.
- Python syntax, XML parsing and manifest references passed.
- Odoo server tests are in `tests/test_exclusive_program.py` (not run locally).
- Deployed on POSTest main; installed module, customer domain, weekly limit form,
  saving/reloading configuration and native report verified in Odoo UI.
- Created QA Exclusive Test Customer and QA Exclusive Customer Discount, scoped
  to that exact customer name, for register testing.
- Register loads after Reload data; employee PIN required to continue.
- Paid-order integration, concurrent-session limits, offline payment rejection
  and Arabic UI verification remain pending. Do not treat these as passed.
