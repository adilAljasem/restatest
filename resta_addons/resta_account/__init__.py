from . import controllers
from . import models
from . import wizard


def post_init_hook(env):
    """Accounting admins get the full accounting menus right away."""
    managers = env.ref("account.group_account_manager").all_user_ids
    env.ref("account.group_account_user").sudo().write({"user_ids": [(4, u.id) for u in managers]})
    env["resta.account.report"]._resta_rename_menus()
