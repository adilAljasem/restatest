from odoo import _, api, models

FOOD = ["appetizers", "salads", "mains", "grills", "desserts"]
DRINKS = ["drinks", "hot_drinks"]


class PosConfig(models.Model):
    _inherit = "pos.config"

    def _resta_dinein_categ(self, keys):
        return self.get_record_by_ref(["resta_edition_dinein.categ_%s" % k for k in keys])

    def _resta_dinein_vals(self):
        vals = super()._resta_dinein_vals()
        vals.update({
            # QR on every table: customer browses the menu and orders, pays at the end
            "self_ordering_mode": "mobile",
            "self_ordering_service_mode": "table",
            "self_ordering_pay_after": "meal",
        })
        cats = self._resta_dinein_categ(FOOD + DRINKS)
        if cats:
            vals.update({"limit_categories": True, "iface_available_categ_ids": [(6, 0, cats)]})
        return vals

    def _resta_dinein_floors(self):
        return self.env["restaurant.floor"].browse(self.get_record_by_ref([
            "resta_edition_dinein.floor_families", "resta_edition_dinein.floor_singles",
        ]))

    @api.model
    def load_onboarding_resta_dinein_scenario(self, with_demo_data=False):
        res = super().load_onboarding_resta_dinein_scenario(with_demo_data)
        config = self.browse(res["config_id"])
        # Replace the generic display created by the base with kitchen + drinks bar
        self.env["resta.kds.display"].sudo().search([("pos_config_ids", "in", config.ids)]).unlink()
        self._resta_prep_displays(config, [
            (_("Kitchen"), self._resta_dinein_categ(FOOD)),
            (_("Drinks Bar"), self._resta_dinein_categ(DRINKS)),
        ])
        return res
