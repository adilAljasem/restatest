Odoo POS Custom Branding
========================

Remove all generic Odoo branding from your Point of Sale and replace it with
your own professional brand. This module targets **Odoo 19 Community** and
extends the POS to let you manage custom logos and branding per POS
configuration.

Main Features
-------------

* Custom POS logo per POS configuration.
* Remove \"Powered by Odoo\" from POS receipts.
* Replace default Odoo logo in the POS navbar/header.
* Replace Odoo logo on the saver / sleep screen.
* Replace the logo and hide \"Powered by Odoo\" text on the customer display
  (customer-facing screen).
* Per-POS settings for:

  * ``Hide Odoo Branding`` boolean.
  * ``POS Brand Logo`` image used in header, saver screen and customer display.

Configuration
-------------

1. Go to :menuselection:`Point of Sale --> Configuration --> Point of Sale`.
2. Open a POS configuration.
3. In the **Branding & Logos** section:

   * Enable **Hide Odoo Branding** if you want to remove Odoo logos/text.
   * Upload an image in **POS Brand Logo** to use as the POS logo.

4. Save the configuration and start a POS session to see the changes.

Receipt configuration
---------------------

1. Set the company logo and Saudi VAT number on the POS company. Configure
   the warehouse/branch contact number and translate product names into
   English and Arabic.
2. In the POS configuration, enable **Custom Receipt** and select
   **Bilingual Thermal Receipt**. Its order type is the POS preset name
   (for example, Dine In, Takeaway or Delivery); the receipt reference is
   the order's ``pos_reference``.
3. Configure the receipt footer and thank-you message in POS settings, save,
   and reopen the POS session so the frontend receives the new settings.
4. Check the receipt after payment and reprint the same order on the
   configured printer. The design follows Odoo's standard 266px browser
   print width and 512px hardware raster width. ZATCA QR generation requires
   the Saudi POS localization and a Saudi company.

Company
-------

* `Axiom Cloudnest Technologies (CloudNest) <https://www.cloudnest.com.ng>`__

CloudNest delivers enterprise cloud, ERP and AI solutions for African businesses,
with focus on Nigeria and West Africa.

Support
-------

* **Email:** support@cloudnest.com.ng
* **Phone:** +234 8138682705
* **WhatsApp:** https://wa.me/message/SLIFHU4GCIRGC1
* **Website:** https://www.cloudnest.com.ng

Further information
===================

* HTML Description: `<static/description/index.html>`__
