Net consumption simplification — staging verification
===================================================

Environment: cove-sa-stg-40531021.dev.odoo.com, company My Company.
Date: 2026-10-25. Dedicated BCA NET fixtures; no production deployment.

* Kit document 35: delivered 10, linked return 2, BOM 2 -> 4.
  Recorded 16, required 32, delta 16, cost 10, signed value 160.
  Actual stock decreased by 16; one balanced consolidated journal entry.
* Document 36: recorded 32, required 32, zero delta; no movement/entry.
* Document 37: BOM 4 -> 3, recorded 32, required 24, delta -8,
  signed value -80. Difference-account balance is -80.
* Document 38: repeated negative correction gives zero; no movement/entry.
* Manufacturing document 39: actual production 10, linked unbuild 2;
  recorded 16, required 32, delta 16, value 160. One net component move.
  Document 40 repeats with recorded 32 and no movement/entry.
* Fully returned kit document 41: sources have opposite individual
  differences, but component net difference is zero. No movement/entry.
  Document 42 repeats with zero. Per-source correction history is retained.
* Gram/kilogram document 43: displayed recorded 16000 g, required 32000 g,
  delta 16000 g. Review cost 10/kg -> 0.01/g and value 160.
  Product cost changed to 12/kg before confirmation; posted value is 192.
  Six decimal places preserve the displayed 0.012/g cost.
* All these documents omit reason and analytic account. Creation, review
  and execution succeed, and journal lines have no analytic distribution.
* Browser document 35: ten Components columns in the requested order.
  All ten appear checked in the native column picker. Hiding Unit Cost
  survives reload; re-enabling restores it. Previous Adjustments and
  Exploded Kit are absent from Components. Standard smart buttons remain.

Upgrade migrates historical detail recorded quantities to include existing
prior adjustments and derives signed display values, without rewriting stock
moves or posted entries. Already reviewed documents return to Draft for a
fresh review using the new signature. Confirmed documents remain confirmed.

Validation: dependency-free calculation/package tests and live staging RPC/UI
checks. The full Odoo TransactionCase suite was not run locally.
