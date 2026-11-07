\## Stock Request

`Request → Submit → Accept → Deliver → Receive`

- Create a request from **My Requests**.
- Select the source and destination warehouses, expected date, products,
  quantities, and reason.
- Submit it to the source warehouse responsible users.
- Acceptance creates a Delivery to Transit and a chained Receipt to the
  destination warehouse.
- Delivery and receipt validation update quantities and status automatically.

\## Direct Transfer

`Direct Transfer → Confirm → Deliver → Receive`

Warehouse responsible users and managers can confirm a Direct Transfer without
the Submit and Accept stages. It uses the same two-step transfer and transit
selection rules as a Stock Request.

\## Transit selection

The destination warehouse Receiving Transit Location is preferred. If it is
empty, Odoo uses the company Default Internal Transit Location. Confirmation is
blocked if neither location is configured.
