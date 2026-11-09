from odoo.http import request, route

from odoo.addons.web.controllers.action import Action


class RestaGrowthAction(Action):

    @route("/web/action/load", type="jsonrpc", auth="user", readonly=True)
    def load(self, action_id, context=None):
        """A direct link to a locked feature opens a friendly page instead of the screen."""
        result = super().load(action_id, context=context)
        if not isinstance(result, dict) or not result.get("id") or not result.get("type"):
            return result
        Feature = request.env["resta.growth.feature"]
        if Feature._user_bypasses_lock():
            return result
        _menus, actions = Feature._locked_ids()
        if (result["type"], result["id"]) not in actions:
            return result
        feature = Feature._feature_for_action(result["type"], result["id"])
        return {
            "type": "ir.actions.client",
            "tag": "resta_growth.locked",
            "name": feature.name or "ميزة مقفلة",
            "target": "current",
            "params": {
                "feature": feature.ui_data()[0] if feature else False,
                "action_name": result.get("name") or "",
            },
        }
