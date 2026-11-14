# -*- coding: utf-8 -*-
{
    'name'    : "Inventory Adjustments | Stock Adjustments | Stock Approval Rule | Inventory Approval Rule ",
    'description' : """
        Odoo Stock Adjustments with Approval Workflow
            This comprehensive Odoo module introduces a robust approval system to your inventory adjustments, providing businesses with enhanced control, accuracy, and accountability over their stock movements. Designed to prevent unauthorized changes and streamline inventory management, it ensures that all adjustments, whether for discrepancies, damages, or new stock entries, undergo a formal review process before being applied.
            Key functionalities of this module include:
            - Role-Based Access Control: Differentiates between 'Stock Adjustment User' and 'Stock Adjustment Manager' roles. Users can create and submit adjustment requests, while Managers are vested with the authority to review, approve, or reject these requests.
            - Mandatory Approval Workflow: Every proposed stock change requires explicit approval from a designated manager. This ensures a double-check mechanism, significantly reducing errors and potential fraud.
            - Intuitive Adjustment Creation: Users can easily initiate new inventory adjustments by specifying the relevant location. The system provides clear visibility into Available Quantity, Quantity On Hand, and allows precise entry of the Counted Quantity.
            - Flexible Product Line Management:
                - Compare Available Quantity, Quantity On Hand, and Counted Quantity for precise adjustments.
                - Maintain control by ensuring that if no changes are made to a line, the stock remains unaffected.
                - Manually add individual products by creating new lines, ideal for new stock discoveries or detailed entries.
                - View Reserved Quantity and, if needed, directly unreserve it within the adjustment process.
                - Efficiently handle serial-tracked products by adding multiple lines at once and assigning individual serial numbers.
            - Advanced Filtering and Loading Options: Facilitates detailed inventory counts by allowing users to filter products by: All products, One product category, One specific product, Manual product selection, or One Lot Number. It also supports loading products with 0 On Hand quantity for a complete stock overview.
            - Automated Notifications: Keeps all stakeholders informed. Users receive notifications upon approval or rejection of their requests, while managers are promptly alerted to pending approvals.
            - Comprehensive Reporting: Generate and export detailed stock adjustment reports to Excel, providing valuable insights for auditing, analysis, and decision-making.
        By implementing the Stock Adjustments with Approval Workflow module, your Odoo system will benefit from heightened inventory integrity, improved operational transparency, and a more controlled stock management process, ensuring your inventory records accurately reflect your physical stock.
    """,
    'category': 'Inventory/Inventory',
    'license' : 'LGPL-3',
    'version' : '19.0.1.7',
    'website' : "https://resta.sa",
    'author'  : "Resta",
    'depends' : ['stock', 'stock_account'],
    'data' : [
        'security/ir.model.access.csv',
        'security/security.xml',
        'views/stock_inventory_template_views.xml',
        'views/inventory_adjustments_form_view.xml',
        'views/stock_quant.xml',
        'views/server_action.xml',
        'wizard/inventory_selection.xml',
        'wizard/stock_report_export.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'inventory_stock_adjustments/static/src/js/*.js',
        ],
    },
    'images': ['static/description/banner.png', 'static/description/icon.png'],
    'installable' : True,
    'auto_install': False,
    'application' : True,
}
