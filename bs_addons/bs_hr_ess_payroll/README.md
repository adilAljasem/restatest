# bs_hr_ess_payroll

Bridge between the ESS suite and **Odoo Enterprise `hr_payroll` (19.0)**. Not auto-installed.

## Features

1. **Adjustments -> payslip inputs.** `hr.payslip.compute_sheet()` first injects every pending
   `bs.hr.payroll.adjustment` (same employee and company, `date <= date_to`, not linked to another payslip)
   as an `hr.payslip.input` line (`ess_adjustment_id` keeps the link). It is idempotent: stale inputs
   (cancelled adjustment, amount changed) are replaced and nothing is duplicated.
   Input type: `hr_payroll.input_deduction` / `hr_payroll.input_reimbursement`, else code
   `DEDUCTION` / `REIMBURSEMENT`, else the shipped `BSDED` / `BSADD` types.
   **The salary structure must have rules that use these input codes**, otherwise the inputs do not reach
   the net. Example rule: `result = -inputs['BSDED'].amount if 'BSDED' in inputs else 0`.
   - Payslip confirmed (`validated`/`done`/`paid`, through `write` or `action_payslip_done`): adjustments become `applied`.
   - Payslip cancelled: adjustments go back to `pending` and are unlinked (the next payslip picks them up).
   - Payslip reset to draft: back to `pending`, still linked to the payslip.
2. **Portal**: `/my/hr/payslips` (year filter, net per year), `/my/hr/payslip/<id>` (lines, the source of
   every adjustment, objections), `/my/hr/payslip/<id>/pdf`, `/my/hr/payslip/<id>/objection`.
   Only confirmed payslips are shown, plus drafts published for review ("Draft for review").
   Pay runs have **Publish for review (ESS)** and **Unpublish (ESS)** in the *Actions* menu. These are server actions,
   because the Enterprise view xmlids could not be checked.
3. **`bs.hr.payslip.objection`** (request mixin): manager (checks attendance) -> `group_ess_payroll_officer`.
   The approver sets `resolution_amount` / `resolution_type` (also from the portal). Approval creates an
   `objection` adjustment for the next payslip.
4. Dashboard card **Last payslip** (net) and the **My payslips** menu entry.

## hr_payroll names: verified vs assumed

The Enterprise source was not available. The names below were checked against public sources on 2026-11-04.

| Name | Status | Source |
|---|---|---|
| model `hr.payslip`; fields `employee_id`, `date_from`, `date_to`, `state`, `number`, `name`, `line_ids`, `company_id` | verified | MobileDoo/mobile_payroll (19.0 branch, reads Enterprise `hr.payslip`) |
| `hr.payslip.line` fields `name`, `code`, `total`, `sequence`, `category_id`; `appears_on_payslip` (used only if present) | verified | MobileDoo/mobile_payroll |
| states `done`, `paid` | verified | MobileDoo/mobile_payroll; odoo/documentation 19.0 `payroll/payslips.rst` (Draft, Validated, Done, Paid, Canceled) |
| status "Validated" (key assumed `validated`), "Canceled" (key assumed `cancel`), `verify` (Waiting) | label verified, key assumed | docs 19.0 `payslips.rst`; `verify` in odoo/odoo PR #29616 (older version). Keys are filtered against the real selection at runtime |
| `compute_sheet()` | verified (older versions) / assumed 19 | odoo/odoo PR #29616; docs 19.0 "Compute Sheet" button |
| `action_payslip_done()` | verified (older versions) / assumed 19 | hibou-io/odoo-us-payroll tests; state changes are also caught in `write` |
| salary input types with a **Code**; rule variable `inputs` keyed by input type code; `version` = `hr.version` | verified | docs 19.0 `payroll/salaries.rst`, `payroll/salary_rules.rst` |
| pay run states Ready / Done / Paid | verified (labels) | docs 19.0 `payroll/pay_runs.rst` |
| groups `hr_payroll.group_hr_payroll_user`, `hr_payroll.group_hr_payroll_manager` | verified | Odoo 19 Community `hr/tests/test_hr_version.py`, `hr/tests/test_ui.py` |
| `hr.payslip.input` fields `payslip_id`, `input_type_id`, `amount`, `name` | assumed (`name` used only if writable, else `note`) | |
| `hr.payslip.input.type` fields `name`, `code`, `active` | assumed (`active` used only if present) | |
| xmlids `hr_payroll.input_deduction`, `hr_payroll.input_reimbursement` | assumed (fallback by code, then BSDED/BSADD) | |
| `hr.payslip.line.slip_id` | assumed (stable since v8) | |
| `hr.payslip.run` model, `slip_ids` (used only if present, else `payslip_run_id` search) | assumed | |
| `hr.payslip.net_wage` | assumed (fallback: line with code `NET`) | |
| report `hr_payroll.action_report_payslip` (then `hr_payroll.action_report_light_payslip`) | assumed (fallback: first qweb-pdf `ir.actions.report` on `hr.payslip`) | |
| `hr.payslip.version_id` (required in 19?) | assumed, tests set it only if the field exists | |

## Validation done

* `py_compile` on all Python files and an lxml parse of all XML files.
* Installed and tested against a **local stub** of `hr_payroll` (same model and field names as assumed above,
  not shipped): `0 failed, 0 error(s) of 6 tests`. **Run the tests again on an Odoo.sh Enterprise branch**
  (`--test-tags /bs_hr_ess_payroll`) before going live. Payslips there may also need a structure / version
  for `compute_sheet` to run.

## Known risks on Enterprise

* If Enterprise recomputes `input_line_ids` (salary adjustments compute) after our injection, manual inputs
  could be dropped. Check this on a real database. "Load ESS adjustments" (Actions menu) re-runs the injection.
* The form views of `hr.payslip` / `hr.payslip.run` are not inherited (unverified xmlids). Buttons are server
  actions in the Actions menu.
