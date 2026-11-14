"""Restaurant systems no longer have a website: the sign-in page is the Resta page and the
public site is the platform's (restaerp.com). The website module and everything built on it
are removed when the restaurant system is updated (the platform database keeps its website).
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Module = env["ir.module.module"]
    if Module.search_count([("name", "=", "resta_saas_master"), ("state", "in", ("installed", "to upgrade"))]):
        return
    website = Module.search([("name", "=", "website"), ("state", "in", ("installed", "to upgrade"))])
    if not website:
        return
    # removed with it: website_sale, website_mail, website_links... (modules that need the website)
    website.with_context(resta_platform_command=True).button_uninstall()
    removed = Module.search([("state", "=", "to remove")]).mapped("name")
    _logger.info("Resta: removing the website from this restaurant system: %s", ", ".join(sorted(removed)))
