from urllib.parse import quote

from odoo.tests import HttpCase, no_retry, tagged


def _hoot_job_hash(name):
    value = 0
    for char in name:
        value = ((value << 5) - value + ord(char)) & 0xFFFFFFFF
    return f"{value:08x}"


def _unit_test_error_checker(message):
    return "[HOOT]" not in message


@tagged("post_install", "-at_install")
class TestProductModifiersFrontend(HttpCase):
    @no_retry
    def test_modifier_hoot_suite(self):
        database = quote(self.env.cr.dbname, safe="")
        module_filter = _hoot_job_hash("@product_modifiers/unit/modifier_utils")
        self.browser_js(
            (
                f"/web/tests?db={database}"
                "&headless&loglevel=2&preset=desktop&timeout=15000"
                f"&id={module_filter}"
            ),
            "",
            "",
            login="admin",
            timeout=60,
            success_signal="[HOOT] Test suite succeeded",
            error_checker=_unit_test_error_checker,
        )
