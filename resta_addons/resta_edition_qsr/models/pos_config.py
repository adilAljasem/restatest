from odoo import _, api, models

FOOD = ["breakfast", "meals", "burgers", "sandwiches", "sides", "desserts"]
BAR = ["hot_coffee", "iced_coffee", "cold_drinks"]


class PosConfig(models.Model):
    _inherit = "pos.config"

    def _resta_qsr_categ(self, keys):
        return self.get_record_by_ref(["resta_edition_qsr.categ_%s" % k for k in keys])

    def _resta_qsr_vals(self):
        vals = super()._resta_qsr_vals()
        vals.update({
            # QR menu on the counter / tables (read-only); switch to "mobile" for order & pay
            "self_ordering_mode": "consultation",
            "self_ordering_service_mode": "counter",
        })
        cats = self._resta_qsr_categ(FOOD + BAR)
        if cats:
            vals.update({"limit_categories": True, "iface_available_categ_ids": [(6, 0, cats)]})
        return vals

    @api.model
    def load_onboarding_resta_qsr_scenario(self, with_demo_data=False):
        res = super().load_onboarding_resta_qsr_scenario(with_demo_data)
        config = self.browse(res["config_id"])
        Display = self.env["resta.kds.display"].sudo()
        Display.search([("pos_config_ids", "in", config.ids)]).unlink()
        # Kitchen and coffee bar get their own screens; "ready" order numbers show on the
        # customer order-status TV (resta_kds tracking page) for 5 minutes.
        self._resta_prep_displays(config, [
            (_("Kitchen"), self._resta_qsr_categ(FOOD)),
            (_("Coffee Bar"), self._resta_qsr_categ(BAR)),
        ])
        Display.search([("pos_config_ids", "in", config.ids)]).write({"tracking_ready_minutes": 5})
        return res
