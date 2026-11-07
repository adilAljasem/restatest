from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestRewardKanbanView(TransactionCase):
    def test_description_is_rendered_on_reward_card(self):
        view = self.env.ref("loyalty.loyalty_reward_view_kanban")
        arch = view._get_combined_arch()

        description_nodes = arch.xpath(
            "//kanban/templates/t[@t-name='card']"
            "/div[@name='reward_info']/div[@name='reward_description']"
            "/field[@name='description']"
        )
        self.assertEqual(len(description_nodes), 1)
