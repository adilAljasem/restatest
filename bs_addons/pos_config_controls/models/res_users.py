from odoo import api, fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    allowed_pos_config_ids = fields.Many2many(
        comodel_name="pos.config",
        relation="res_users_allowed_pos_config_rel",
        column1="user_id",
        column2="pos_config_id",
        string="Allowed POS",
        help="Point of Sale configurations available to this user when Restricted POS User is enabled.",
    )

    @api.model
    def _get_invalidation_fields(self):
        return super()._get_invalidation_fields() | {"allowed_pos_config_ids"}
