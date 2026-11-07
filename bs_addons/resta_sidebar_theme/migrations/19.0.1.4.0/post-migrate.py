from odoo import api, SUPERUSER_ID

from odoo.addons.resta_sidebar_theme.hooks import apply_email_button_color


def migrate(cr, version):
    apply_email_button_color(api.Environment(cr, SUPERUSER_ID, {}))
