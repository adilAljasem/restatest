from . import models
from . import controllers


def post_init_hook(env):
    """Give the installing admin access so the menu is not a dead end.

    Membership is written from the *user* side on purpose. Odoo 19 dropped
    ``res.groups.users`` entirely, and the reverse field on ``res.users`` was
    renamed ``groups_id`` -> ``group_ids`` in the same release, so the field
    name is resolved at runtime rather than assumed.
    """
    group = env.ref('the_dash.group_the_dash_manager', raise_if_not_found=False)
    admin = env.ref('base.user_admin', raise_if_not_found=False)
    if not (group and admin):
        return
    Users = env['res.users']
    field = 'group_ids' if 'group_ids' in Users._fields else 'groups_id'
    admin.sudo().write({field: [(4, group.id)]})


def uninstall_hook(env):
    """Remove the app-menu entries and give Odoo's spreadsheet dashboards back their root."""
    env['the.dash.app.menus']._unlink_all()
