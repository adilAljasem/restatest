from odoo import fields, models


class ResUsers(models.Model):
    """Which branches a user may see in The Dash.

    The limit is enforced in the controller, not in the browser: the scope
    panel only lists permitted branches, *and* any ``config_ids`` arriving in
    the query string is intersected with the same set. Hand-editing the URL
    cannot widen what a user sees.
    """
    _inherit = 'res.users'

    dash_config_ids = fields.Many2many(
        'pos.config',
        'the_dash_user_config_rel', 'user_id', 'config_id',
        string='The Dash: allowed points of sale',
        help='Points of sale this user may see in The Dash.\n'
             'Leave empty to allow every branch.\n'
             'Ignored for members of "The Dash / Manager", who always see all.',
    )

    @property
    def SELF_READABLE_FIELDS(self):
        # Guarded: this hook has moved around between majors, and a missing
        # base attribute here would break every self-read of a user record.
        base = getattr(super(), 'SELF_READABLE_FIELDS', None) or []
        return list(base) + ['dash_config_ids']
