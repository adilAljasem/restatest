{
    'name': 'The Dash',

    'summary': (
        'On-demand point of sale analytics. One link, every branch, no shop '
        'picker. Manual refresh only - it never polls and never slows a till.'
    ),

    'description': """
The Dash
========

The system dashboard ("لوحة البيانات") of the Resta suite, in the Resta theme:
point of sale analytics plus an Operations section fed by every installed app -
inventory, purchasing, accounting, manufacturing, people and guest NPS - opened
inside the back office or full screen on a wall.

19.0.4.0
--------
- Becomes the dashboard app: first menu entry, embedded in the back office
  (client action) with a full-screen entry for wall screens.
- Operations route and card (/thedash/ops) with one tab per installed app;
  sections are hidden when their app is missing and fail independently.
- "لوحة البيانات" entry in the reporting menu of Inventory, Purchase,
  Accounting, Manufacturing, Employees, Attendance and Sales, opening the
  matching tab; Odoo spreadsheet dashboards filed under it.
- Resta theme: light by default, Resta dark for wall screens, IBM Plex Sans
  Arabic, Resta lockup.

Standalone
----------
The Dash does not touch the point of sale front end. It adds no button to the
POS navbar and loads nothing into the POS asset bundle: it is its own app,
opened from its own menu.

One link, every branch you may see
----------------------------------
There is no shop picker and no per-shop token. **The Dash** menu opens
``/thedash`` with every permitted point of sale already included; narrowing
happens inside the page.

Per-user branch permissions
---------------------------
- **The Dash / Viewer** may open The Dash, and sees the branches listed on
  their own user record under the *The Dash* tab. An empty list means all.
- **The Dash / Manager** always sees every branch, and is shown the generated
  SQL if a report fails.
- The limit is enforced on the server. The scope panel lists only permitted
  branches, and a ``config_ids`` handed to the URL is intersected with the
  same set, so editing the address bar cannot widen what a user sees.

Nothing runs until you ask
--------------------------
- The page arrives **empty**. No query runs on open, there is no polling and
  no background timer touching the database. Tills are never slowed down.
- The dashboard and the multi-layer report have **separate refresh buttons and
  separate routes**, so neither can stall or break the other.
- Each reports **how long it took** - server time and full round trip - plus
  when it ran and how stale it now is. A performance panel breaks the duration
  down per section.
- Changing a filter runs nothing; it marks the affected view stale and waits.

Two themes
----------
A deep dark theme with a gold accent (default, built for wall screens) and a
clean light theme with a teal accent. The choice is remembered per browser.

Figures
-------
- **KPIs** with period-over-period comparison: net sales, orders, customers,
  average order, refunds, cancellations. Days follow the user's timezone.
- **Business day** (pos_business_day): hours, "today" and every period follow
  the company's business-day start hour and timezone; the hourly chart runs
  from the start hour (06:00 ... 05:00) and shows the first and last order
- **Sales by hour** - sales / orders / average, split by order type or by
  payment kind - and **by POS category**
- **Order type**: dine-in, takeaway, delivery and delivery apps (from the
  Odoo 19 preset, the table, or the delivery hub), with a mix by hour
- **Trend by business day** and a weekday x hour peak grid
- **Item profitability from recipe cost** (mrp bills of materials, sub-recipes
  and units of measure; combos from their items' recipes), with every product
  sold without a recipe - or with an ingredient without cost - listed with a note
- **Delivery-app commissions** from Connect (bs_pos_delivery_core): per app,
  recorded vs estimated from the connection rate, by business day, and
  profitability by channel; spread over the items of each app order and
  counted as a variable cost in break-even
- **Menu engineering** (Kasavana & Smith): stars, plowhorses, puzzles and dogs
  from popularity and contribution after recipe and commission, overall or
  within each category, with a recommendation per item
- **Actual vs recipe usage** on its own route and button: recipes exploded to
  stocked ingredients against what left the branch warehouses (sales,
  production, scrap, counts) - theoretical vs actual food cost and waste
- **Break-even** on its own route and button: operating expenses from the
  accounting (actual for the period, or the average of the last 3 months)
  against orders, contribution per order from recipe cost, break-even orders,
  safety margin and four charts
- **Charts** for collections, payment methods, apps, shortages / overages
  (by POS, cashier, business day and session) and kitchen service time
- **Item sales**: main items only, combos at their full price (their items
  folded in), then **modifier sales** (pos_restaurant_combo_modifiers and
  product_modifiers) and the attribute options with their extra price.
  Items + modifiers = order lines total.
- **Collections**: cash, bank, each delivery app (bs_pos_delivery_core or
  FeedUs; an app order counts for its app whatever method settled it) and
  customer account, plus every payment method.
- **By point of sale, cost center, region and cashier**: sales, refunds,
  cash / bank / apps, sessions, expected and counted cash, shortages,
  overages, cash and non-cash closing differences.
- **Closings**: every session of the period with its expected and counted
  cash and its differences.
- **Service time from the kitchen display** (Odoo 19 pos.prep and Odoo 17-18
  pos_preparation_display): average, median, 90th percentile, longest, late
  orders, by point of sale, display and hour, slowest orders.
- **Sales by delivery app** and **by order type**, when those fields exist

Multi-layer report - 19 measures
--------------------------------
Gross sales, discount, net sales, tax, net sales with tax, cost, profit,
orders, customers, guests, average per order, average per guest, net quantity,
refund quantity, refund amount, cancelled amount, cancelled quantity, tips,
and share of net. Columns are user-selectable and remembered per browser.

Group by branch (sales team), point of sale, cost center, region, cashier
(POS employee when pos_hr is installed), customer, category, product template,
product variant, combo, line type (item / modifier / combo item), modifier,
attribute option, discount, order source, order type,
promotion or coupon - with an optional second level adding combo item, day,
week, month, quarter, year, hour of day, day of week, day of month, day of
year, week of year, month of year and quarter of year.

Dimensions and measures your database cannot provide are hidden automatically
rather than reported as zero.

Performance
-----------
Every dashboard figure is aggregated in SQL through ``_read_group``; the whole
report is a **single SQL statement** regardless of how many measures or
dimensions are selected. No order or order-line recordsets are ever loaded
into memory. A refresh costs a fixed number of queries whatever the data size.

If the report fails, the database message is shown inside the report card -
with the generated SQL for administrators - instead of a blank panel.
    """,

    'version': '19.0.4.0.0',
    'category': 'Point of Sale',
    'author': 'BASIC Solution',
    'maintainer': 'BASIC Solution',
    'support': '',   # TODO: support e-mail
    'license': 'LGPL-3',
    'website': '',   # TODO: company website

    'depends': ['point_of_sale', 'web', 'analytic'],

    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'views/region_views.xml',
        'views/pos_config_views.xml',
        'views/res_users_views.xml',
        'views/menu.xml',
    ],

    'images': ['static/description/banner.png'],
    'external_dependencies': {'python': ['xlsxwriter']},
    'post_init_hook': 'post_init_hook',
    'uninstall_hook': 'uninstall_hook',
    'assets': {
        'web.assets_backend': [
            'the_dash/static/src/backend/*',
        ],
    },
    'installable': True,
    'application': True,
}
