import re

from odoo import api, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    @api.depends("company_id")
    def _compute_company_informations(self):
        """The Saudi address fields are added even when empty («Building Number: False»): drop those."""
        super()._compute_company_informations()
        for record in self:
            info = record.company_informations or ""
            info = re.sub(r"[^,\n:]+:[ \t]*False[ \t]*,?[ \t]*", "", info)
            record.company_informations = "\n".join(line.strip(" ,") for line in info.splitlines()
                                                    if line.strip(" ,"))
