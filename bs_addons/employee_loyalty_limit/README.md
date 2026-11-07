# Employee Promotion Limits

This Odoo 19 add-on extends **Discount & Loyalty** promotions used in Point of
Sale. A promotion can be restricted to customers linked to `hr.employee` and
limited independently per employee for a day, week, or month.

The add-on records one usage per employee, promotion program, and paid POS
order. It refreshes usage when the cashier selects a customer, validates again
before payment, and performs an authoritative server-side check while saving
the order. The standard program-wide **Limit Usage** setting is not used.

Dependencies: `hr` and `pos_loyalty`.

Enable **For Employees** to display **Employee Domain**. Use the domain builder
to select employees by Department, Tags, individual employees, or combined
AND/OR conditions. An empty domain allows all employees. Conditions apply to
the employee linked through the customer's work contact and are checked both
in the POS and on the server before accepting a promotion.

After deployment, upgrade `employee_loyalty_limit` and reload POS sessions.

Employee promotions require cashier confirmation when selecting the customer.
Each eligible employee promotion displays its name and asks whether to apply
the discount. Declining or closing the popup keeps it off for that order;
changing quantities does not enable it. Select the employee again to reconsider.
Approval is specific to the order, employee, and program, and is cleared when
the customer changes. Other promotions keep their standard behavior.
