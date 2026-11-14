from odoo import _, api, fields, models


class PosConfig(models.Model):
    _inherit = "pos.config"

    # Every POS in Resta is a restaurant POS.
    module_pos_restaurant = fields.Boolean(default=True)
    use_presets = fields.Boolean(default=True)
    iface_printbill = fields.Boolean(default=True)
    iface_splitbill = fields.Boolean(default=True)
    resta_edition = fields.Selection(
        [("dinein", "Dine-in Restaurant"), ("qsr", "Fast Food & Cafe")],
        string="Resta Edition",
        help="Which pre-configured Resta edition this point of sale was created from.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals["module_pos_restaurant"] = True
        return super().create(vals_list)

    def write(self, vals):
        if "module_pos_restaurant" in vals and not vals["module_pos_restaurant"]:
            vals = dict(vals, module_pos_restaurant=True)
        return super().write(vals)

    # ------------------------------------------------------------------
    # Onboarding scenarios (cards on the empty POS dashboard)
    # ------------------------------------------------------------------
    def _resta_prep_displays(self, config, displays):
        """Resta kitchen screens (resta_kds). displays: list of (name, [pos.category ids])"""
        Display = self.env["resta.kds.display"].sudo()
        for name, categ_ids in displays:
            Display.create({
                "name": name,
                "company_id": config.company_id.id,
                "pos_config_ids": [(6, 0, config.ids)],
                "category_ids": [(6, 0, categ_ids)],
            })

    def _resta_common_vals(self, name, cash_name):
        journal, payment_method_ids = self._create_journal_and_payment_methods(
            cash_journal_vals={"name": cash_name, "show_on_dashboard": False},
        )
        return {
            "name": name,
            "company_id": self.env.company.id,
            "journal_id": journal.id,
            "payment_method_ids": payment_method_ids,
            "module_pos_restaurant": True,
            "iface_splitbill": True,
            "iface_printbill": True,
        }

    def _resta_finalize(self, config, xmlid):
        # Namespace "resta_onboarding" (not a module) so the record is not garbage
        # collected when the scenario runs from an install hook.
        self.env["ir.model.data"]._update_xmlids([{
            "xml_id": self._get_suffixed_ref_name(xmlid.replace("resta_saas_base.", "resta_onboarding.")),
            "record": config,
            "noupdate": True,
        }])
        # Presets menu visible for everybody, like the standard restaurant scenario
        self.env.ref("point_of_sale.group_pos_preset").implied_by_ids |= self.env.ref("base.group_user")
        return {"config_id": config.id}

    # -- Dine-in -----------------------------------------------------------
    def _resta_dinein_vals(self):
        vals = self._resta_common_vals(_("Dine-in Hall"), _("Cash - Hall"))
        presets = self.get_record_by_ref([
            "pos_restaurant.pos_takein_preset",
            "pos_restaurant.pos_takeout_preset",
            "resta_saas_base.preset_delivery_apps",
        ])
        vals.update({
            "resta_edition": "dinein",
            "default_screen": "tables",
            "use_presets": bool(presets),
            "default_preset_id": presets[0] if presets else False,
            "available_preset_ids": [(6, 0, presets)],
        })
        return vals

    def _resta_dinein_floors(self):
        return self.env["restaurant.floor"]

    @api.model
    def load_onboarding_resta_dinein_scenario(self, with_demo_data=False):
        config = self.create(self._resta_dinein_vals())
        floors = self._resta_dinein_floors()
        if floors:
            config.floor_ids = [(6, 0, floors.ids)]
        self._resta_prep_displays(config, [(_("Kitchen"), [])])
        return self._resta_finalize(config, "resta_saas_base.pos_config_dinein")

    # -- Fast food / cafe --------------------------------------------------
    def _resta_qsr_vals(self):
        vals = self._resta_common_vals(_("Counter"), _("Cash - Counter"))
        presets = self.get_record_by_ref([
            "pos_restaurant.pos_takeout_preset",
            "pos_restaurant.pos_takein_preset",
            "resta_saas_base.preset_drive_thru",
            "resta_saas_base.preset_delivery_apps",
        ])
        vals.update({
            "resta_edition": "qsr",
            "default_screen": "register",
            "use_presets": bool(presets),
            "default_preset_id": presets[0] if presets else False,
            "available_preset_ids": [(6, 0, presets)],
            "iface_printbill": False,
            "iface_splitbill": False,
        })
        return vals

    @api.model
    def load_onboarding_resta_qsr_scenario(self, with_demo_data=False):
        config = self.create(self._resta_qsr_vals())
        config.floor_ids = [(5, 0, 0)]
        self._resta_prep_displays(config, [(_("Kitchen"), [])])
        return self._resta_finalize(config, "resta_saas_base.pos_config_qsr")

    # Retail scenarios are not part of Resta
    @api.model
    def load_onboarding_retail_scenario(self, with_demo_data=False):
        return self.load_onboarding_resta_qsr_scenario(with_demo_data)
