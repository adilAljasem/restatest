from . import models


def post_init_hook(env):
    env["pos.config"].search([("module_pos_restaurant", "=", True)])._sfs_sync_trusted()
