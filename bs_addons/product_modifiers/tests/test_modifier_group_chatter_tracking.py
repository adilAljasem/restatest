from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestModifierGroupChatterTracking(TransactionCase):
    def test_group_creation_and_name_change_are_logged(self):
        group = self.env["modifiers.groups"].create({
            "name": "Tracked Modifier Group",
        })

        self.assertIn("message_ids", group._fields)
        self.assertIn("activity_ids", group._fields)
        self.assertTrue(group._fields["name"].tracking)
        self.assertTrue(group._fields["modifier_ids"].tracking)
        self.assertTrue(any(
            "Modifiers group created." in (message.body or "")
            for message in group.message_ids
        ))

        group.write({"name": "Updated Modifier Group"})

        self.assertTrue(any(
            message.tracking_value_ids
            and "Modifiers group changed." in (message.body or "")
            for message in group.message_ids
        ))
