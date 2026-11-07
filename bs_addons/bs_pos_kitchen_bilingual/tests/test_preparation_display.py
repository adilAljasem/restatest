from odoo import Command
from odoo.addons.pos_enterprise.tests.test_frontend import (
    TestPreparationDisplayHttpCommon,
)
from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestBilingualPreparationDisplay(TestPreparationDisplayHttpCommon):
    def test_bilingual_product_name_is_rendered(self):
        self.env["res.lang"]._activate_lang("ar_001")
        self.letter_tray.with_context(lang="ar_001").write({
            "name": "صينية مستندات",
        })
        self.pdis.write({
            "category_ids": [Command.set(self.letter_tray.pos_categ_ids.ids)],
        })

        self.main_pos_config.with_user(self.pos_user).open_ui()
        self.start_pos_tour("PreparationDisplayTour")
        self.start_pdis_tour("BilingualPreparationDisplayTour")

