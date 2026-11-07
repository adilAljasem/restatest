from . import models


def post_init_hook(env):
    """Enable source availability on a new installation by default."""
    env["ir.config_parameter"].sudo().set_param(
        "stock_request.show_available_qty",
        True,
    )
