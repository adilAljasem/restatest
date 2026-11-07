################################################################################
#
#    Axiom Cloudnest Technologies
#
#    Copyright (C) 2026-TODAY Axiom Cloudnest Technologies.
#    Author: Axiom Cloudnest Technologies
#
#    This program is free software: you can modify
#    it under the terms of the GNU Affero General Public License (AGPL) as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
################################################################################
{
    'name': 'Odoo POS Custom Branding',
    'version': '19.0.1.9.1',
    'category': 'Point of Sale',
    'live_test_url': 'https://www.youtube.com/watch?v=sHQUam5F5Qs',
    'summary': "POS Custom Branding, Remove Odoo Branding, Custom POS Logo, "
               "POS Receipt Design, Custom Receipt, POS Branding, Odoo19",
    'description': "Remove Odoo branding from POS and customize with your own logo. "
                   "Upload custom logos for POS header, customer screen, and lock screen. "
                   "Also includes custom receipt design options.",
    'author': 'Axiom Cloudnest Technologies',
    'company': 'Axiom Cloudnest Technologies',
    'maintainer': 'Axiom Cloudnest Technologies',
    'website': 'https://www.cloudnest.com.ng',
    'depends': ['base', 'point_of_sale', 'l10n_sa_pos'],
    'data': [
        'security/ir.model.access.csv',
        'data/pos_receipt_design1_data.xml',
        'data/pos_receipt_design2_data.xml',
        'data/bilingual_thermal_receipt_data.xml',
        'data/pino_sales_receipt_data.xml',
        'views/pos_receipt_views.xml',
        'views/pos_config_views.xml',
        'views/res_config_settings_views.xml',
        'views/pos_receipt_settings_views.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'odoo_pos_custom_branding/static/src/scss/bilingual_receipt.scss',
            'odoo_pos_custom_branding/static/src/scss/pino_sales_receipt.scss',
            'odoo_pos_custom_branding/static/src/scss/receipt_paper.scss',
            'odoo_pos_custom_branding/static/src/scss/print_area.scss',
            'odoo_pos_custom_branding/static/src/scss/pino_print.scss',
            'odoo_pos_custom_branding/static/src/js/bilingual_receipt_order.js',
            'odoo_pos_custom_branding/static/src/js/receipt_pattern_data.js',
            'odoo_pos_custom_branding/static/src/js/receipt_logo_cache.js',
            'odoo_pos_custom_branding/static/src/js/print_wait_images.js',
            'odoo_pos_custom_branding/static/src/js/order_number_range.js',
            'odoo_pos_custom_branding/static/src/js/pino_sales_receipt_order.js',
            'odoo_pos_custom_branding/static/src/js/receipt_design.js',
            'odoo_pos_custom_branding/static/src/js/pos_branding.js',
            'odoo_pos_custom_branding/static/src/xml/order_receipt.xml',
            'odoo_pos_custom_branding/static/src/xml/pos_branding.xml',
        ],
        # Customer display has separate asset bundle
        'point_of_sale.customer_display_assets': [
            'odoo_pos_custom_branding/static/src/js/customer_display_branding.js',
            'odoo_pos_custom_branding/static/src/xml/customer_display_branding.xml',
        ],
    },
    'images': ['static/description/banner.png'],
    'license': 'AGPL-3',
    'pre_init_hook': 'pre_init_hook',
    'installable': True,
    'auto_install': False,
    'application': False,
}
