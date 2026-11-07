from odoo import _, api, models
from odoo.exceptions import ValidationError


COMPANY_REQUIRED_FIELDS = (
    "street",
    "street2",
    "vat",
    "city",
    "state_id",
    "zip",
    "country_id",
)

LOCALIZED_COMPANY_REQUIRED_FIELDS = (
    "l10n_sa_edi_building_number",
    "l10n_sa_edi_plot_identification",
)


class ResPartner(models.Model):
    _inherit = "res.partner"

    @api.model_create_multi
    def create(self, vals_list):
        normalized_vals_list = []
        for values in vals_list:
            values = dict(values)
            if "company_type" in values:
                values["is_company"] = values["company_type"] == "company"
            normalized_vals_list.append(values)
        return super().create(normalized_vals_list)

    def write(self, values):
        values = dict(values)
        if "company_type" in values:
            values["is_company"] = values["company_type"] == "company"
        return super().write(values)

