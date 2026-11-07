from . import models
from . import wizard


def post_init_hook(env):
    """Generate closing reports retroactively for every closed session on install."""
    sessions = env["pos.session"].search([("state", "=", "closed")])
    env["pos.closing.report"]._generate_for_sessions(sessions, source="retro")
