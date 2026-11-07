# Bonat Loyalty for Odoo Online (`pos_bonat_loyalty_online`)

An **importable** build of `pos_bonat_loyalty` for **Odoo Online (SaaS)** databases,
where third-party Python code cannot be deployed.

> Odoo Apps sales conditions state that third-party applications "can NOT be
> installed on Online (SaaS) databases". That is true of ordinary addons. It is not
> true of *importable* modules (also called data modules), which Odoo's own
> developer documentation describes as being for "managed hosting solutions which
> do not allow the deployment of custom Python code like the Odoo.com platform".
> This module is one of those, and it installs and runs on Odoo Online. Verified
> on a live trial.

Listed on the **Odoo Apps Store** since 2026-08-04 (free; 19.0 and 17.0):
https://apps.odoo.com/apps/modules/19.0/pos_bonat_loyalty_online — merchants download the
zip there and import it via *Apps → Import Module* (developer mode). The listing is served
from `Bonat-Org/pos-bonat-loyalty-store#19.0`, kept in sync by `odoo-store-sync 19.0` after
each merge to this branch (see `CONTRIBUTING.md` → "Syncing the Online builds to the Odoo
Apps Store"). A zip built straight from this directory installs the same way.

---

## Upgrade — Basic Solution, 2026-10-31 (several codes per order, used at kitchen send)

### What changed for the cashier

* **Several Bonat codes on one order**, Foodics-style: whole-order discounts,
  per-product rewards and vouchers can be combined. Before, the second code was
  refused ("Bonat Code Already Applied"). The same code is still refused twice,
  on the same order or on another open order of the POS.
* **A code is only checked when entered** (reward-check). It is **used with Bonat
  (redeem) when the order is sent to the kitchen**, in the background: the
  kitchen send and the POS never wait for Bonat.
* A code still unused at payment (order never sent to the kitchen, or code
  added afterwards) is used at payment — all such codes in parallel, one wait.
  A discount is never given for a code Bonat did not accept.
* If Bonat refuses a code (already used elsewhere), the cashier gets a warning
  right after the kitchen send, and payment is blocked with a dialog that
  removes the refused codes (and their discount) in one tap. If Bonat is only
  unreachable, the code is retried at payment.
* Each Bonat line shows its code and status: *not used yet* / *used* /
  *refused by Bonat*. Receipts show the code only.
* Removing codes: Backspace on the Bonat discount line asks which whole-order
  code to remove (or all); a code already used asks for confirmation first,
  since removing it does not give it back. Deleting the free items of a
  per-product code removes that code. Deleting the voucher payment line of
  vouchers not used yet removes them.

### How

* `bonat_codes.js` (new, pure, unit-tested in `tests/bonat_codes.test.mjs`):
  the code list (`order.bonatCodes`), states, stacking math.
* Whole-order codes stack one after the other on what is left
  (10% then 5 SR on 100 SR = 15; two 50% codes = 50 + 25) and are written as
  **one** discount through Odoo's own `prepare_global_discount_lines` — still one
  line per tax group, VAT exact, 100% still lands on 0.00.
* The list travels with the order in a new manual field `pos.order.x_bonat_codes`
  (`data/bonat_order_fields.xml`, JSON with a revision number): the waiter's
  device and the cashier's device see the same codes and states. After a
  kitchen-send redeem the states are written to the saved order at once; at
  payment the latest states are read back before anything is redeemed again.
* Kitchen hook: `PosStore.sendOrderInPreparation` and
  `sendOrderInPreparationUpdateLastChange` (what the Order button calls).
* Redeem sends `method` (`Discount` / `Voucher`) and, for vouchers,
  `payment_amount` / `payment_method`, per Bonat's POS Integration Guide v3.7.

### Bugs fixed on the way

* **Bonat discount lines not recognised after a reload or on another device.**
  Odoo 19 caches model getters; `isBonatDiscountLine` was cached as `false`
  before the Bonat config loaded, so the discount was then treated as an
  ordinary line (discount of the discount on the next edit). The Bonat getters
  are now excluded from that cache.
* Discount product loading failed (`unhashable type: 'dict'`) when it was not
  already in the POS: `data.read()` returns records on 19.0, the template id is
  now taken from the record.
* "… discount on 1 qty up to 0.00" no longer printed when there is no cap.

### Tested

Odoo 19.0 Community + `pos_restaurant`, Bonat calls stubbed with a 1.5 s
redeem: 35/35 end-to-end checks (three codes on one order, kitchen send
measured at ~150 ms while redeem takes 1.5 s, payment with no Bonat call,
payment without kitchen send, refused code, per-code removal, page reload,
two devices, two vouchers, refused voucher).

## Upgrade — Basic Solution, 2026-10 (stability + tax-correct discounts)

### 1. Whole-order (type 1) discount was wrong after VAT

Symptom: a 100% code on a 12.00 SR item (15% VAT, price-included) left
**1.57 SR** to pay (discount line −10.43, taxes 0.21).

Cause: the discount was summed from `line.priceExcl` (tax-excluded, 10.43) but
put on a line whose price Odoo treats as tax-included, so the VAT part was
never discounted.

Fix: the discount is now produced by Odoo's own global-discount engine,
`accountTaxHelpers.prepare_global_discount_lines()` — the same code core
`pos_discount` uses. One discount line per tax group, with matching taxes and
manual tax amounts (`extra_tax_data`, a core `pos.order.line` field the server
already honours). Results, checked against the real 19.0 helper:

| Case | Before | After |
|---|---|---|
| 12.00 SR, 100% | 1.57 | **0.00** |
| 12.00 SR, 50% | — | 6.00 |
| 49.00 SR, 10% capped at 3 | — | 46.00 |
| 12.00 SR, fixed 50 | — | 0.00 (never negative) |
| 12@15% + 7@0%, 100% | — | 0.00 |

All amounts are tax-included (what the customer sees); `max_discount_amount`
0/absent means uncapped.

The rule is stored on the order (`bonatDiscountRule`, IndexedDB-persisted) and
the discount is **recomputed automatically** when the order changes (items
added/removed, qty, preset/pricelist/fiscal position), like core
`pos_discount`. Removing an item after a 100% code no longer leaves a negative
total. Qty/price/discount numpad modes are disabled on the Bonat line;
Backspace/Delete removes the whole Bonat discount.

Type 2 (per-product) rewards now use Odoo's native `discount` field instead of
rewriting `price_unit`, so receipts/reports show a real discount and 100% is
exactly 0.00. `max_discount_amount = 0` no longer silently gives no discount.

### 2. POS freezes

* **Every payment waited for Bonat** (up to 10 s, or the full timeout when
  Bonat/internet was slow). The order notification now runs *after* Odoo
  validates, fire-and-forget.
* Hooks moved from `PaymentScreen.validateOrder` to core
  `OrderPaymentValidation` (`bonat_validation.js`), so the **fast payment
  buttons** (Cash / Visa / مدى on the product screen) are now covered too —
  before, those orders were never reported to Bonat and skipped the voucher
  guard. A voucher order paid with a fast button is refused cleanly and the
  stray payment line removed.
* **POS opening** waits at most 6 s for Bonat; slower config reads continue in
  the background. The two `pos.config` reads run in parallel.
* Reward check and redeem show Odoo's standard busy overlay, so no double taps
  or order edits mid-request.
* `PaymentScreen.onMounted` is synchronous again (core never awaits it).
* On 19.0 (Odoo.sh / on-premise) the discount product is loaded on demand via
  core's `load_product_from_pos`; it no longer needs "Available in POS".

## The constraint that shapes everything

`base_import_module._import_module()` allows exactly **two** Python files in the
package: an empty `__init__.py` and `__manifest__.py`. Nothing else. So every
model, field, controller and server method in the on-premise module has to be
re-expressed as XML records or browser-side JavaScript.

It also **rejects asset paths containing `*` or `**`**. The on-premise manifest
declares `pos_bonat_loyalty/static/src/**/*`, which would be refused outright.
Every asset here is enumerated by hand, and **any new static file must be added to
`__manifest__.py` or it will silently not load**.

---

## Two platform differences that are easy to get wrong

### 1. Odoo Online runs Owl 3, and template scope changed

`saas~19.4` ships **Owl 3.0.0-alpha.45**. In Owl 3, template expressions no longer
fall back to the component instance. Component members must be reached through an
explicit `this.`:

```xml
<!-- Owl 2, as written on-premise. Silently renders NOTHING on Odoo Online. -->
<t t-esc="bonatVoucherLabel"/>
<div t-if="bonatDemoModeActive"/>

<!-- Owl 3, required here -->
<t t-esc="this.bonatVoucherLabel"/>
<div t-if="this.bonatDemoModeActive"/>
```

Only `t-set` and `t-foreach` locals may be referenced bare, which is why core
templates read `vals.price` but `this.line.order_id`.

The failure mode is nasty: literals still render, so a template looks half-alive
while every dynamic binding evaluates to nothing. No error is raised. An earlier
feasibility spike misdiagnosed exactly this as "imported modules are sandboxed
from component scope" and concluded the UI had to be rebuilt imperatively in
JavaScript. That conclusion was wrong. Templates work normally once the `this.`
prefix is used, and this module keeps its UI fully declarative.

### 2. Manual fields must start with `x_`, and related ones need `store="False"`

Odoo rejects a manual field whose name does not begin with `x_`, so the field names
here are **not** the same as on-premise:

| On-premise (`res.company`) | Here |
|---|---|
| `enable_bonat_integration` | `x_bonat_enabled` |
| `bonat_api_key` | `x_bonat_api_key` |
| `bonat_merchant_id` | `x_bonat_merchant_id` |
| `bonat_merchant_name` | `x_bonat_merchant_name` |
| `bonat_demo_mode` | `x_bonat_demo_mode` |
| (only in `ir.config_parameter`) | `x_bonat_api_url`, `x_bonat_api_url_staging` |
| `pos.config.bonat_discount_percentage_product_id` | `pos.config.x_bonat_discount_product_id` |

The `res.config.settings` mirrors carry `related="company_id.x_..."` **and**
`store="False"`. `ir.model.fields.store` defaults to `True` and Odoo does **not**
derive it from `related`; left at the default, each field becomes a real stored
column on the transient table, `default_get` has nothing to populate it from, and
the whole settings block renders blank behind
`invisible="not x_bonat_enabled"`. This was hit during development, on a real
database. Do not remove those lines.

---

## Mapping: every on-premise unit and where it went

### Python that became XML

| On-premise | Here |
|---|---|
| `models/res_company.py` fields | `data/bonat_company_fields.xml` |
| `models/pos_config.py` field | `data/bonat_pos_config_fields.xml` |
| `models/res_config_settings.py` related fields | `data/bonat_settings_fields.xml` |
| `data/config_parameters.xml` | `data/bonat_config_parameters.xml` (see note) |
| `views/res_config_settings_view.xml` | `views/bonat_settings_view.xml` |

### Python that became JavaScript

| On-premise | Here |
|---|---|
| `res.company.get_bonat_code_response()` | `BonatApi.rewardCheck()` |
| `res.company._bonat_demo_response()` | `BonatApi.demoResponse()` |
| `res.company._get_bonat_api_base_url()` | `BonatApi.baseUrl` + `bonat_store.js` |
| `pos.session.pos_reward_redeem()` | `BonatApi.redeem()` |
| `pos.session.pos_order_creation_request()` | `BonatApi.orderCreated()` |
| `res.company._load_pos_data_fields()` | `bonat_store.js → loadBonatConfig()` |
| `product.template._load_pos_data_domain()` | `bonat_store.js → loadBonatDiscountProduct()` |
| `res.config.settings.action_bonat_test_connection()` | `settings/bonat_test_connection.js` |
| `..._bonat_notify()` | the same file, via the `notification` service |
| `..._onchange_enable_bonat_integration()` | `settings/bonat_enable_field.js` |
| `..._toggle_api_key_visibility()` + `bonat_show_api_key` | `settings/bonat_api_key_field.js` |
| `const.py` (URLs, demo codes, timeouts) | `app/bonat_const.js` |

`res.company._load_pos_data_fields()` cannot be replaced in kind: manual `x_`
fields are **not** included in the preloaded POS payload. Measured on a live
database, a payload read of such a field returns `undefined` while an ORM read of
the same field succeeds. So `bonat_store.js` does one explicit read at POS startup
and caches the result on `pos.bonat`.

`product.template._load_pos_data_domain()` force-loaded the discount product
despite `available_in_pos = False`. Replaced by `pos.data.loadProductFromPos()`,
the standard client-side way to pull a product into the registry on demand. The
product still stays out of the cashier's grid.

### Python that was deliberately dropped

| On-premise | Why it is not here |
|---|---|
| `controllers/main.py` (`/api/pos/products`, `/categories`, `/configs`, `/sessions`, `/orders`) | Inbound only: these let **Bonat read data out of Odoo**. Odoo's standard external API already exposes those models and is available on Odoo Online. This becomes a Bonat-backend change, not a module change. |
| `controllers/binary.py` (`/api/image/...`) | Superseded by the standard `/web/image/<model>/<id>/<field>` route. |
| `models/ir_http.py` (`_auth_method_bonatapi`) | Only existed to authenticate the controllers above. |
| `const.py` field lists (`product_fields`, `pos_order_fields`, …) | Only consumed by those controllers. |
| `migrations/19.0.8.1/pre-migrate.py` | Fixes stale view records on the existing production database. A new module has no such history. |
| `tests/test_bonat_demo_mode.py` | Python tests cannot ship in an importable module. Keep them in the internal repo against the on-premise build. |
| `security/ir.model.access.csv` | Was header-only, defining nothing. |

**Action required on the Bonat side:** the five `/api/pos/*` endpoints are the
route Bonat used to read merchant catalogue and order data. Bonat's backend must
switch to Odoo's external API (`/web/dataset/call_kw`, or XML-RPC) with an API key,
for Odoo Online merchants.

### JavaScript, and what the POS API rewrite forced

Business logic carries over. Several patch targets no longer exist.

**Important correction.** Most of these removals are **not** Odoo Online specific.
They are already true of the current **19.0** build running on `odoo19.bonat.io`
(`19.0-20260630`), verified by reading the core source on that server. Odoo moved
the POS pricing and serialization APIs inside the 19.0 series, after this module
was written. See "Comparison with the 19.0 production server" at the end of this
file. Only the last three rows below are genuinely platform differences.

| Removed on saas~19.4 | Replacement used here |
|---|---|
| `PosOrder.serialize()` | `serializeForIndexedDB()` |
| `PosOrder.export_for_printing()` | dropped, see below |
| `PosOrderline.serialize()` | `serializeForIndexedDB()` |
| `PosOrderline.getDisplayData()` | `Orderline.lineScreenValues` (component getter) |
| `PosOrderline.get_all_prices()` | line splitting, see below |
| `PosOrderline.compute_all()`, `_doRecomputeAllPrices()` | not needed once lines are split |
| `pos.taxes_by_id`, `pos.get_taxes_after_fp()` | not needed once lines are split |
| `line.comboParent` | `line.combo_parent_id` |
| `line.get_quantity()`, `get_unit_price()`, `get_discount()` | `getQuantity()`, `price_unit`, `discount` |
| `ProductScreen` template anchor `t-slot="default"` | `ul.info-list` |
| `t-esc="line.unitPrice"` anchor | `lineScreenValues.displayPriceUnit`, overridden in JS |
| settings anchor `block[@id='pos_inventory_section']` | `block[@id='pos_pricing_section']` |

`export_for_printing()` copied the Bonat fields onto the receipt payload, but no
receipt template in either build reads them, so dropping it loses nothing. The
values are persisted via `serializeForIndexedDB` so an applied code survives a
refresh. They are deliberately **not** added to `serializeForORM`: there are no
Bonat columns on `pos.order` in either build, and Bonat learns about the order from
a direct API call at validation time.

---

## Partial per-quantity discounts: the one behavioural change

On-premise, a type=2 reward covering fewer units than a line's quantity was handled
by a ~220-line `get_all_prices()` override that reimplemented Odoo's tax engine to
price part of a line at a discount and the remainder at full price.

That engine is gone on `saas~19.4`, replaced by the unified `account.tax`
JavaScript port behind `prices` / `getBaseLine()`. There is no equivalent seam, and
rebuilding a tax engine against an internal API would be the most fragile thing in
this module.

Instead, `applyType2Discount()` adds **one dedicated line per rewarded product**,
carrying exactly the rewarded quantity, and discounts that line in full. No split
is ever needed, so core pricing is used unmodified and totals, taxes, receipts and
accounting all follow standard behaviour.

Consequences:

- A pre-existing line of the same product is left untouched. On-premise the second
  pass also processed it, double-counting the reward against the cap. **This is a
  fix.**
- The `max_discount_amount` cap is still applied cumulatively across products, in
  the order the popup lists them.
- `discountAmount -= totalDiscountApplied`, present on-premise, is dropped: it
  subtracted a cumulative currency total from a per-unit fixed amount, giving every
  product after the first a meaningless discount. The cap is enforced by `headroom`.

The `percentage_partial_discount` / `fix_amt_partial_disc` annotation fields are
retained so restored orders still render their explanatory row.

---

## Two things Bonat must provide

Neither is an Odoo problem, and neither can be solved inside this module.

### CORS

Without controllers there is no server-side proxy, so the POS calls Bonat straight
from the browser at `https://<db>.odoo.com`. The Bonat API must:

- return `Access-Control-Allow-Origin` for `*.odoo.com`
- allow `Authorization` and `Content-Type` on the preflight
- answer `OPTIONS` on `/odoo_partner/reward-check`, `/redeem` and `/order`

Until then every call fails at the network layer. A CORS rejection is
indistinguishable from a network outage in the browser, so Test Connection names
both causes.

### A browser-safe credential

The API key is read from `res.company` by the POS client, so **it is present in the
browser and readable by any cashier who can open devtools**. On-premise it never
left the server. This is a real regression and it is inherent to having no
server-side code.

Bonat should issue a POS-scoped public key, or a short-lived per-session token.
`BonatApi.authHeader` is the single choke point that needs to change; nothing else
in the module touches the credential.

---

## Install

```bash
python3 tools/build_online_zip.py     # writes pos_bonat_loyalty_online.zip
```

The archive's single top-level entry must be `pos_bonat_loyalty_online/`, because
Odoo derives the technical name from it. The script handles the rename from this
repo's `odoo 19 online/` directory.

On the target database:

1. *Settings → General Settings → Developer Tools* → activate developer mode.
2. *Apps* → install **Base Import Module** if it is not already present.
3. *Settings → Technical → Import Module* → upload the zip → **Install**.
   Re-importing the same zip performs an upgrade.
4. *Settings → Point of Sale → Bonat Integration* → switch on, enter the API key,
   Merchant ID and Merchant Name, and pick a **Bonat Discount Product**.
5. *Point of Sale → Payment Methods → New* → create a non-cash payment method,
   add it to the POS's payment methods, then return to *Bonat Integration* and
   select it as the **Bonat Voucher Payment Method**. Required before the first
   voucher redemption — this module does not create a payment method for you;
   until one is selected, voucher reward codes are declined at scan time.
6. Optionally switch on **Demo Mode** and try codes `1`, `2`, `3`.

The Bonat Discount Product should be a **service** product with **Available in
POS** switched off, so it never appears in the cashier's grid. It is required for
whole-order (type 1) reward codes.

---

## Five more platform traps, each found by running it

Every one of these produced no build error and no console error. They are recorded
because each cost a debugging cycle and each is easy to reintroduce by copying the
on-premise file.

1. **Model records have no `pos` and no `env`.** `PosOrderline` / `PosOrder` are
   Proxies exposing `models`, `config` and `session` only. The on-premise
   `this.pos.notification.add(...)` in `setQuantity()` throws
   `Cannot read properties of undefined (reading 'notification')`, which the cashier
   sees as Odoo's generic "Oops!". Fixed with `bonat_runtime.js`.

2. **A blocked `setQuantity()` must return `true`.** The numpad path surfaces any
   non-`true` result in its own dialog and uses the value as the body, so returning
   `false` renders an empty "Alert" popup on top of our own notification.

3. **Lazy resolution beats setup-time resolution.** Orders are restored from
   IndexedDB during `processServerData()`, which runs *before*
   `afterProcessServerData()` publishes the config. A recovered order resolved its
   merchant identity too early and kept `""` for the whole session, so Bonat would
   have received a blank `business.reference`. The merchant getters now fall back at
   read time.

4. **No `_t()` in `static defaultProps`.** Class bodies are evaluated before the POS
   translation catalogue loads, so `_t()` yields a lazy string that renders as empty.
   The popup's Apply and Cancel buttons came out as 27px blank blocks. Translate in a
   getter instead.

5. **`%%` is not an escape in JS `_t()`.** Odoo's JS translation uses named
   `%(placeholder)s` substitution and does not unescape `%%`, so the Python habit
   printed a literal "10%% discount".

Plus the two structural ones already covered above: `block[@id='pos_inventory_section']`
does not exist on this series, and related manual fields need explicit
`store="False"`.

## Verified on a live Odoo Online trial (`saas~19.4`)

Database `mehdi-mosbah.odoo.com`, Demo Mode on, TND currency.

| Item | Result |
|---|---|
| Import accepted, module state | **installed**, `saas~19.4.1.0` |
| Series prefix from omitted `version` key | confirmed, Odoo supplied `saas~19.4` |
| 16 manual fields created | pass, across `res.company`, `pos.config`, `res.config.settings` |
| Related `store="False"` fields resolve | pass, including `pos_config_id.x_bonat_discount_product_id` |
| Settings block renders, all 8 fields | pass |
| API key masked, eye toggle reveals | pass (`password` → `text`) |
| Test Connection widget renders | pass |
| Startup config load (`pos.bonat`) | pass, company fields read over RPC |
| Demo Mode banner in Product Screen | pass |
| "Use Bonat Coupon" control button | pass, correct styling |
| Demo code `1` (whole order, 10%) | pass, `Bonat Discount` line at −14.000 DT on a 140.00 base; total 166.600 → 152.600 |
| Discount product loaded despite `available_in_pos = False` | pass, and absent from the product grid |
| Demo code `2` (per-product picker) | pass, popup with 5 products, cap counter, `+` disabled at the allowance |
| Type-2 discount applied | pass, dedicated line qty 1, unit 39.270 → 35.343 incl. |
| Pre-discount unit price still shown | pass, via the `lineScreenValues` override |
| Discount note row in `ul.info-list` | pass, "39.270 DT with a 10% discount on 1 qty up to 20.000 DT" |
| Qty lock on Bonat lines | pass, warning shown, quantity unchanged, no stray dialog |
| Qty 0 escape hatch, then re-lock | pass |
| Order state survives reload (IndexedDB) | pass, lines, code and merchant identity all restored |
| Console errors attributable to this module | none |

Not exercised: the three live API calls against a real Bonat tenant. They need the
CORS work above before they can succeed from a browser, so only the Demo Mode paths
(which short-circuit before `fetch`) have been run end to end.

---

## Comparison with the 19.0 production server

Measured read-only against `odoo19.bonat.io` (`odoo19_prod`, `19.0-20260630`,
Community) on 2026-08-03, with `pos_bonat_loyalty 19.0.8.25` installed, by reading
the core source files it serves under `/point_of_sale/static/src/`.

### Genuinely different between the two platforms

| | 19.0 (`odoo19.bonat.io`) | Odoo Online (`saas~19.4`) |
|---|---|---|
| Owl | **2.8.2** | **3.0.0-alpha.45** |
| Owl 2 compat layer | absent | present |
| Template scope | bare `line.foo` works | **`this.` required** |
| Core `orderline.xml` bindings | `line.order_id`, `vals.*` (0 of 22 use `this.`) | `this.line.order_id`, `vals.*` |
| `t-slot="default"` in Orderline | **present** | **absent** |
| POS settings block | `pos_inventory_section` | `pos_localization_section` |
| Third-party Python | allowed | forbidden |
| Manifest `version` | must pin `19.0.x` | must be omitted |

The core-template row is the useful one: Odoo had to migrate **its own** templates
from bare identifiers to `this.` between these two builds. That is the Owl 3
scoping change, seen from the outside, and it is why the module's bare bindings
render as nothing on Odoo Online while working fine on 19.0.

### Already true on 19.0 as well, so not an Online problem

Every one of these is absent from the current 19.0 core, exactly as on `saas~19.4`:

| Symbol | 19.0 core | Consequence for the deployed module |
|---|---|---|
| `get_all_prices` | absent | the ~220-line override is dead code; `patch()` adds the method but core never calls it |
| `compute_all` | absent | only reachable from the dead override |
| `getDisplayData` | absent | override is dead; core uses `lineScreenValues` |
| `PosOrder.serialize` / `PosOrderline.serialize` | absent | base class has `serializeForIndexedDB` / `serializeForORM` instead, so both overrides are dead and the Bonat fields are not persisted |
| `export_for_printing` | absent | override is dead |
| `line.comboParent` | absent (`combo_parent_id`) | the type=1 base-amount loop reads `undefined`, so combo children are no longer excluded |
| `this.pos` on model records | never used by core; no back-reference | `this.pos.notification` in the qty-lock path throws |
| `t-esc="line.unitPrice"` in core Orderline | **`unitPrice` appears nowhere** | the `position="replace"` xpath has no anchor |

`lineScreenValues`, `models/accounting/`, `getBaseLine` and
`prepareBaseLineForTaxesComputationExtraValues` are all **present on 19.0**, i.e.
the modern API this Online build targets is already the 19.0 API too.

### What that implies for `pos_bonat_loyalty` on 19.0

Not verified by running the POS, deliberately: `odoo19_prod` had an **open session**
(`Johani/00047`) at the time of checking, so the POS UI was not loaded.

The timeline is worth knowing:

| Event | Date |
|---|---|
| `point_of_sale` last written | 2026-06-24 |
| Server build | `19.0-20260630` |
| `pos_bonat_loyalty 19.0.8.25` deployed | **2026-07-30** |
| Last POS session and order | **2026-07-27** |

The POS has not been opened since the current module version was deployed, so none
of the above would have surfaced yet. The missing `line.unitPrice` xpath anchor is
the one to check first, since a `position="replace"` against an element that does
not exist is the failure most likely to be hard rather than silent.

**This needs its own ticket against the on-premise module.** It is out of scope for
this PR, which only adds the Online build, and it should be reproduced on a staging
copy rather than on `odoo19_prod`.

---

## Can this build run on a self-hosted 19.0 instance?

Technically almost entirely yes, but **you should not use it there**. Checked against
`odoo19.bonat.io` (19.0-20260630).

### What is compatible

| Dependency | On 19.0 |
|---|---|
| `this.`-prefixed template expressions | **works** — Owl 2.8.2 accepts them, and core 19.0 uses `this.pos` in its own ProductScreen and ControlButtons templates |
| `div.rightpane`, `div.control-buttons-modal`, `ul.info-list` anchors | all present |
| `block[@id='pos_pricing_section']` | present |
| `serializeForIndexedDB` / `serializeState` / `restoreState` | present |
| `lineScreenValues`, `displayPriceUnit`, `displayPriceUnitNoDiscount` | present |
| `combo_parent_id`, `setQuantity`, `setUnitPrice` | present |
| `afterProcessServerData`, `addLineToOrder`, `pos.notification` | present |
| `data.read`, `data.searchRead` | present |
| no `pos` / `env` on model records | same, so the `bonat_runtime.js` bridge is needed and correct there too |
| manual `x_` fields, omitted manifest `version`, explicit assets, plain CSS | all fine |

Templates being forward and backward compatible is the useful part: writing `this.`
works on both Owl 2 and Owl 3, so the prefix is the safe style to standardise on.

### The one incompatibility

`pos.data.loadProductFromPos()` **does not exist on 19.0** (its data service has
`read`, `searchRead`, `call`, `silentCall` and `callRelated` only). Whole-order
(type 1) codes therefore cannot pull an `available_in_pos = False` discount product
into the registry. `loadBonatDiscountProduct()` detects this and logs the fix;
everything else, including type-2 codes, keeps working.

Workaround on 19.0: tick **Available in POS** on the Bonat discount product. It is
then part of the normal POS load and the fast path resolves it. The only cost is
that it shows up in the cashier's product grid.

### Why you should still not deploy it on-premise

Three things get **worse**, all of them consequences of having no server-side code:

1. **The `/api/pos/*` endpoints disappear.** If Bonat's backend reads catalogue,
   session or order data from those routes on this instance, swapping modules
   breaks that integration.
2. **CORS becomes a hard dependency.** On-premise the calls originate from the
   Odoo server, so no CORS is involved. This build calls Bonat from the browser.
3. **The API key moves into the browser**, where any cashier can read it. On-premise
   it never leaves the server.

Also: the two modules **must not be installed together**. Both patch the same POS
components, so you would get two "Use Bonat Coupon" buttons, two banners and two
independent sets of configuration (`enable_bonat_integration` vs `x_bonat_enabled`).

### What is worth taking from here instead

The JavaScript layer. This build is written against the modern POS API — the same
API the current 19.0 now has — whereas `pos_bonat_loyalty` still targets the older
one and consequently has dead `get_all_prices` / `getDisplayData` / `serialize` /
`export_for_printing` overrides, a `this.pos.notification` call that throws, and a
`position="replace"` xpath whose anchor no longer exists.

So the productive direction is to backport this module's JS into
`pos_bonat_loyalty` while keeping all of its Python: the controllers stay, the API
calls stay server-side, no CORS, no exposed key. That fixes on-premise without
inheriting any of the three downsides above.
