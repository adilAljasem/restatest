## Module <odoo_pos_custom_branding>

#### 30.10.2024
#### Version 18.0.1.0.0
##### ADD
- Initial Commit for POS Receipt Design

#### 04.12.2024
#### Version 18.0.1.0.1
##### BUG FIX
- BUG FIX

#### 28.05.2025
#### Version 18.0.1.0.2
##### BUG FIX
- Fixed the issues in the custom receipts which show error while printing the receipts.

#### 06.08.2025
#### Version 18.0.1.0.3
##### BUG FIX
- Fixed the issues in the custom receipts which show error while loading the POS receipts screen.

#### 29.10.2025
#### Version 18.0.1.0.4
##### BUG FIX
- Fixed the POS issue displaying the default receipt despite a selected custom design.

#### 18.11.2025
#### Version 18.0.1.0.5
##### BUG FIX
- Fixed the POS issue where the receipt design was not appearing even if a design is selected.

#### 01.10.2026
#### Version 19.0.1.0.7
##### IMPROVEMENT
- Puls (RYVK) thermal receipt: printer raster now uses the full 576-dot width of 80mm paper with equal side margins (content no longer shifted left).
- Logo spans the full receipt width.
- Order number printed in a large bordered box; invoice reference printed bold under the bilingual title.
- Bilingual (Arabic/English) totals: before VAT, VAT with rate, total incl. VAT, and payment method lines.
- ZATCA QR printed large and centered at the bottom without the explanatory text box.

#### 02.10.2026
#### Version 19.0.1.0.8
##### IMPROVEMENT
- Puls (RYVK) receipt: bilingual column headers (Qty, Item, Unit Price, Incl. VAT); new unit price (excl. VAT) column and line amount including VAT.

#### 02.10.2026
#### Version 19.0.1.0.9
##### IMPROVEMENT
- Puls (RYVK) receipt: "Powered by basicsolution.net" line at the bottom.

#### 02.10.2026
#### Version 19.0.1.1.0
##### ADD
- Point of Sale > Configuration > Receipt Settings (إعدادات الفاتورة): per-POS toggles for invoice sequence number, order number, unit price column, English item names, English totals labels, header lines and footer lines (with editable texts).
- Arabic is the base language of the Puls (RYVK) receipt; English lines are optional.
- Arabic translations (i18n/ar.po).

#### 09.10.2026
#### Version 18.0.1.2.0
##### ADD
- Puls (RYVK) receipt: delivery card on delivery orders — platform name (`aggregator`) and platform order ID (`aggregator_order_id`). Shown only when the order has delivery data or uses a preset that requires an address; toggle "Show Delivery Order Details".
- Puls (RYVK) receipt: order type printed without the table number; POS name (نقطة البيع) printed under the date.
- Puls (RYVK) receipt: identity pattern band at the top and/or bottom of the receipt, printed in solid black for thermal printers. Built-in pattern or a custom uploaded image (Receipt Settings > Identity Pattern).
- Puls (RYVK) receipt: Arabic only (English item names, headers and labels removed); POS name and customer on one row; vertical rules between detail cells and between the item columns.

##### PORT TO ODOO 18
- Manifest version `18.0.1.2.0`: Odoo 18 refused `19.0.x` versions ("invalid manifest").
- Receipt data computed in `PosOrder.export_for_printing()` (Odoo 18 receipts only receive the printed data): `pino_receipt_data` and `bilingual_receipt_data`; ZATCA QR taken from l10n_sa_pos.
- Odoo 18 order / line / payment getters; order type from the delivery platform, the `takeaway` flag or the table.
- Arabic product names loaded on `product.template` (`name_ar`), Arabic payment method names through `receipt_name_ar`.
- Translated names are only read for installed languages (no "Invalid language code" error when Arabic is not active).
- Navbar / lock screen logo templates and imports adapted to Odoo 18; customer display keeps the company logo when no brand logo is available.
- Puls (RYVK) receipt: order type from the order's `pos_order_type_name`; cash change printed as «المتبقي» (negative cash payments are no longer listed as payments); customer service phone removed from the top; branch name and address at the top; product count and dish count under the items.

#### 10.10.2026
#### Version 18.0.1.2.1
##### FIX
- Logo missing on some printed receipts: the company logo is downloaded once when the POS starts and embedded in the receipt (data URL) instead of being fetched while the receipt is converted to an image.
- Receipt cut off on the right: new per-POS setting Receipt Settings > Printer > Printer Print Width. Standard (default) prints a 512-dot image / 72 mm page, which every 80 mm printer prints entirely; Full width keeps the previous 576-dot / 78 mm layout.
- Rows can no longer grow wider than the receipt (long notes, large print font).

#### Version 18.0.1.2.2
##### FIX
- Browser printing (no IoT / ePOS printer): wait until the receipt images (logo, pattern, QR) are loaded and drawn before opening the print dialog; some receipts were printed with an empty space instead of the logo.
- Logo cached at 576px width (lighter, faster to draw).

#### Version 18.0.1.2.3
##### FIX
- Logo missing on the first print (shown on reprint): the server prepares a small grayscale print copy of the company logo (`receipt_logo_image`, max 512px, ~20 KB instead of ~340 KB) loaded with the POS, so the logo is available instantly like the identity pattern.
- Receipt printers (ePOS / IoT): one warm-up image conversion before the printed one (images can be missing from the first html-to-image conversion on some browsers, e.g. Safari / iPad).

#### Version 18.0.1.2.4
##### FIX
- Order type: also read `order_type_name`, set on the device by codusic_pos_order_types before the order is saved (the receipt printed "—" right after payment).

#### Version 18.0.1.2.5
##### FIX
- Kitchen tickets cut on the right on 58 mm preparation printers: new Paper Width (80 mm / 58 mm) on Point of Sale > Configuration > Preparation Printers; 58 mm prints a 384-dot image instead of 512.
- The warm-up image conversion runs only for receipts that contain images (not kitchen tickets).

#### Version 18.0.1.2.6
##### FIX
- Kitchen tickets cut on the right since 18.0.1.2.3: printing of receipts without images (kitchen / preparation tickets) on 80 mm printers is again exactly Odoo's own code path (no warm-up, no class change). The warm-up copy used for receipts with images is removed from the print container right after use.

#### Version 18.0.1.2.7
##### FIX
- Kitchen tickets cut on the right: the print area (`.render-container`) is left-to-right again, as maeen_pos_custom_receipt did before it stopped loading (it depends on the old `odoo-pos-custom-branding` name). The Puls receipt keeps its own right-to-left direction.
- Removed the Paper Width setting on preparation printers (18.0.1.2.5): the module no longer changes kitchen printer settings.

#### Version 18.0.1.2.8
##### IMPROVEMENT
- Puls receipt: attribute values printed under the item name, one per line ("Attribute: Value") with their extra price, and the notes under them.
- Puls receipt: tighter spacing (shorter receipt), QR code at 50% width.

#### Version 18.0.1.2.9
##### FIX
- Logo missing on some printed receipts (e.g. delivery orders printed automatically while the iPad app is in the background): after the receipt is converted to the printer image, the logo is drawn directly on that image at its place. The warm-up conversion is no longer needed and was removed. Kitchen tickets are unchanged.
- Waiting for images no longer relies on requestAnimationFrame (paused in background apps).
- Payments: only negative cash payments are the change («المتبقي»); other payments are added up per method (delivery platforms sending +39, +39, -39 now print one line of 39 and no change).

#### Version 18.0.1.2.10
##### FIX
- Attributes under the item: also the values of the product variant itself (attributes that create variants, e.g. "دجاج مطبي (ربع)"), not only the values chosen on the line.
- product_modifiers: modifier lines (linked to their main item by `parent_line_id`) are printed under the main item with their price instead of as separate rows; a modifier whose main item is not found stays a normal row.

#### Version 18.0.1.2.11
##### FIX
- «المتبقي» printed double (860 instead of 430 for 500 paid on 70): the change is now paid minus total instead of the sum of the change lines, which can be on the order twice.

#### Version 18.0.1.2.12
##### FIX
- «المتبقي» only for cash payments on orders that are not from a delivery app; delivery app payments (deferred sales) and cards never show change. A non-cash payment method never prints more than the invoice total.

#### Version 19.0.1.2.12
##### PORT TO ODOO 19
- Same features and fixes as 18.0.1.2.12 for Odoo 19: Puls receipt data shared with the Odoo 18 version (works with both APIs), read through `props.data.pino_receipt_data`; logo stamped on the printer image; print area left-to-right for kitchen tickets; Odoo 19 order presets used as order type.

#### 15.10.2026
#### Version 19.0.1.5.0
##### IMPROVEMENT
- Puls receipt: back to the original design of 19.0.1.1.0 (no dashed top / bottom lines, no branch lines, no product / dish counts). Functional features kept: delivery card on delivery orders, attribute values and modifiers with their extra price and the notes under the item, change for cash only, reliable logo, printer print width.
- Receipt Settings: the unused "Identity Pattern" group is hidden.

##### FIX
- Order number always 3 digits (001 ... 999) on the POS screen, the customer receipt and the kitchen ticket. Odoo 19 put the device number in front ("5001").
- Order Number Range (Receipt Settings): start 1 or more, end 999 at most; numbers padded to 3 digits (1-100 -> 001 ... 100). Range off: 001 ... 999 then 001 again. Orders created on the server follow the same rule.
- Migration: existing POS with an end number over 999 are set to 999.

#### 15.10.2026
#### Version 19.0.1.6.0
##### IMPROVEMENT
- Clear, dark print (idea of pos_kitchen_ticket_custom): bold plain font, larger minimum text sizes, thicker rules, pixel sizes made for the printer image, and the receipt image is turned pure black and white before printing (Receipt Settings > Sharp Black Print). The logo keeps its shades.
- Receipt size and margins: new default width 576 dots (80 mm, like the kitchen ticket) with equal 16-dot margins; options 512 and 420 (58 mm). Existing POS on 512 are moved to 576. New setting Print Font Size (small / normal / large).
- Modifiers, attribute values and notes under the main item, for both modifier modules: pos_restaurant_combo_modifiers (modifier lines, included + paid parts merged, "instructions" such as "بدون: بصل") and product_modifiers (`parent_line_id` as id, uuid or record, `modifier_parent_uuid`). Modifiers of a combo item go under that combo item. Also for orders reprinted from the server.
- Delivery app orders (bs_pos_delivery_core / Connect): delivery card with only the app name and the app order number (the short number shown by the app, else its order ID). FeedUs fields still supported. No dependency on the connector.
- POS name printed next to the customer name.

#### 16.10.2026
#### Version 19.0.1.7.0
##### FIX
- Puls receipt: English labels (Qty, Item, Unit Price, ...) are no longer translated by Odoo into Arabic (`t-translation="off"`).
- Puls receipt: Blend delivery orders (bs_pos_blend_intgeration) print the delivery card (platform + app order number); Bilingual receipt shows the Blend platform as customer.
- Works with bs_pos_blend_intgeration >= 19.0.1.37.0, which no longer wraps the receipt in a second `pos-receipt` (print sizes, paper width and logo were lost on Blend orders).

#### 16.10.2026
#### Version 19.0.1.8.0
##### CHANGE
- Puls receipt: customer phone hidden by default. New setting Receipt Settings > Top of the Receipt > Show Customer Phone.
- Order number: the kitchen ticket (pos_kitchen_ticket_custom >= 19.0.1.4.0) now prints exactly the receipt's order number.

#### 23.10.2026
#### Version 19.0.1.8.2
##### FIX
- Delivery app orders (Blend) printed on half of the paper: the receipt design is printed by itself when another module wraps it in a second `.pos-receipt`.
- The print copy of the receipt logo is stored (prepared once when the company logo changes) instead of being re-processed at every POS load.

#### 24.10.2026
#### Version 19.0.1.9.0
##### ADD
- Puls receipt: cashier who opened the order («فتح الطلب») and cashier who closed it («أغلق الطلب»), on a row under the customer / point of sale row.
  - Opened: `pino_opened_by_name` (bs_pos_session_handover), else `opened_by_name` (pos_table_opened_by), else the order's cashier.
  - Closed: `pino_closed_by_name` (bs_pos_session_handover), else the cashier set on the order at payment (pos_hr). Not printed on a bill printed before payment.
- Per POS settings: Receipt Settings > Top of the Receipt, and Point of Sale settings > Bilingual Custom Receipt: "Show Cashier Who Opened the Order" and "Show Cashier Who Closed the Order" (on by default).
