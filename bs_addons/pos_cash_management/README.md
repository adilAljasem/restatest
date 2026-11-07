# POS Cash Management (Odoo 19)

Merges `pos_cash_bank_transfer` and `pos_cash_difference_employee_partner`
into a single module and ports it to Odoo 19.

## Options

All options live under **Point of Sale → Configuration → Settings → Accounting**
and are set **per Point of Sale**. Labels and messages follow the user
interface language (English source, Arabic translation shipped in `i18n/ar.po`).

| Option | Effect |
|---|---|
| **Mandatory Cash Count** | Pressing *Close Register* with a Cash Count of zero raises the error `Please enter the actual Cash Count before closing the session.` immediately — before the *Payments Difference* dialog, so its *Proceed Anyway* escape is never reached. Applies to every user, managers included. A session where no cash ever moved can still be closed on zero. |
| **Blind Cash Count** | The theoretical balance, the opening balance, the cash moves, the live difference, the auto-fill button and the sales total are stripped **from the server response**, not only hidden in the interface. Applies to every user, managers included. |
| **Transfer Cash to Bank on Closing** | Books the counted cash as a cash out with a configurable reason, once the session is closed, leaving the drawer at zero. The closing report keeps the amounts as they were at counting time. |
| **Cashier Partner on Cash Difference** | Sets the cashier's work contact as the partner of the cash difference journal item (loss/profit line only, never the cash line). |

## Version 19.0.2.0.0

**Mandatory Cash Count — error is now immediate.** The check moved from
disabling the *Close Register* button to `confirm()`, so pressing the button
raises the error at once, ahead of the *Payments Difference* dialog. The
server keeps the same rule as the real guard, and
`get_closing_control_data` now carries a `pcm_has_cash_activity` flag so the
popup can apply the rule without being told any amount (it stays correct in
blind mode). The manager exemption is gone: the rule applies to everyone.

**Enforce Maximum Difference on Server — removed.** Field, setting, server
validation and translations are all gone; `migrations/19.0.2.0.0/post-migration.py`
drops the `pos_config.pcm_enforce_max_diff` column and its `ir.model.fields`
rows. Odoo's own *Set Maximum Difference* is untouched.

**Transfer Cash to Bank — closing report fixed.** The cash out used to be
created inside `post_closing_cash_details`, which fed back into the
theoretical balance and made the report read `expected 53.55 / counted 0.00`.
It now runs at the end of `_validate_session`, once the session is closed:
while a session is closed, `cash_register_balance_end` is computed from the
stored `cash_real_transaction` rather than from the live statement lines, so
the cash out cannot disturb it. For 203.55 expected against 150 counted the
report reads:

```
Closing expected:   203.55
Closing counted:    150.00
Closing difference: -53.55
Cash Out (transfer):  150.00   → drawer 0.00
```

The journal items are unchanged: the shortage still hits the loss account,
the transfer still empties the cash account.

## Notes on the Odoo 19 port

* `try_cash_in_out` gained a `partner_id` argument and now requires cash-move
  rights that a plain cashier does not have. The transfer line is therefore
  created directly through `_prepare_account_bank_statement_line_vals`, so a
  cashier can close their own register.
* `odoo.osv.expression.AND` is deprecated in 19; `odoo.fields.Domain.AND` is
  used instead.
* `pos.config` fields are loaded to the front end automatically in 19, so the
  `_load_pos_data_fields` override of the old module is no longer needed.
* The closing popup moved to
  `@point_of_sale/app/components/popups/closing_popup/closing_popup`.
* Blind count also clears `amount_per_employee` and `moves_per_employee`,
  added by `pos_hr`, which would otherwise leak the expected amounts back.

## Dependencies

`point_of_sale`, `pos_hr`

## Tests

```
odoo-bin -d <db> -u pos_cash_management --test-enable --test-tags /pos_cash_management
```

18 tests, all green on Odoo 19. The 107 core `point_of_sale` tests also stay
green with this module installed.
