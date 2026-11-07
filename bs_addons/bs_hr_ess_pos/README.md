# ESS POS bridge

* POS settings: link each point of sale to its HR branch and tick *Cashier must be checked in* —
  `open_ui` refuses to open the POS for a user without an open attendance at that branch (POS managers
  are exempt).
* Reporting > *Labor cost vs sales*: daily worked hours × hourly cost (employee *Hourly cost*, or wage/240)
  against POS net sales (excl. tax) per branch, with the labor/sales ratio.
