from . import controllers
from . import models


def post_init_hook(env):
    env["res.company"]._resta_brand_apply()
