import uuid

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests.common import new_test_user, tagged

from ..services.auth import BlendAuthService
from ..services.exceptions import BlendAuthenticationError
from .common import BlendTransactionCase


@tagged("post_install", "-at_install")
class TestBlendTokenRevocation(BlendTransactionCase):
    def setUp(self):
        super().setUp()
        self.portal = self._user("base.group_portal")
        self.administrator = self._user("base.group_system")
        self.config.blend_login_user_id = self.portal
        self.login_token = self.config._blend_issue_login_token()

    def _user(self, groups):
        return new_test_user(
            self.env, login="blend-revoke-%s" % uuid.uuid4().hex, groups=groups,
            company_id=self.env.company.id,
            company_ids=[Command.set(self.env.company.ids)],
        )

    def _branch(self, company=None):
        company = company or self.env.company
        suffix = uuid.uuid4().hex
        return self.env["pos.config"].with_company(company).create({
            "name": "Token revocation branch %s" % suffix,
            "company_id": company.id, "blend_enabled": True,
            "blend_branch_id": suffix, "blend_brand_id": self.brand_id,
            "blend_login_user_id": self.portal.id,
            "payment_method_ids": [Command.clear()],
        }).with_env(self.env)

    @staticmethod
    def _snapshot(configs):
        configs.invalidate_recordset(["blend_login_token_hash", "blend_login_token_issued_at"])
        return {
            config.id: (config.blend_login_token_hash, config.blend_login_token_issued_at)
            for config in configs
        }

    def _assert_denied_unchanged(self, configs, user, *, companies=None):
        before = self._snapshot(configs)
        target = configs.with_user(user).with_context(
            allowed_company_ids=(companies or self.env.company).ids,
        )
        self.assertFalse(target.env.su)
        with self.assertRaises(AccessError):
            target.action_revoke_blend_login_token()
        self.assertEqual(self._snapshot(configs), before)

    def test_portal_cannot_revoke_an_unreadable_branch_via_public_method(self):
        self.assertFalse(self.config.with_user(self.portal).has_access("read"))
        self._assert_denied_unchanged(self.config, self.portal)

    def test_public_user_cannot_revoke(self):
        self._assert_denied_unchanged(self.config, self.env.ref("base.public_user"))

    def test_pos_users_and_managers_cannot_revoke_even_with_branch_write_access(self):
        for group in ("point_of_sale.group_pos_user", "point_of_sale.group_pos_manager"):
            with self.subTest(group=group):
                user = self._user(group)
                self.assertFalse(user.has_group("base.group_system"))
                self.assertTrue(self.config.with_user(user).has_access("write"))
                self._assert_denied_unchanged(self.config, user)

    def test_administrator_revokes_only_login_token_and_new_login_still_works(self):
        auth = BlendAuthService(self.env)
        self.assertIn(self.config, auth.authorized_configs("Bearer " + self.login_token))
        values = {field: self.config[field] for field in (
            "blend_login_user_id", "blend_inbound_token", "blend_outbound_token",
            "blend_enabled", "blend_branch_id", "blend_brand_id",
        )}
        target = self.config.with_user(self.administrator)
        self.assertFalse(target.env.su)
        self.assertTrue(target.action_revoke_blend_login_token())
        self.assertEqual(self._snapshot(self.config)[self.config.id], (False, False))
        self.assertFalse(self.config._blend_check_inbound_token(self.login_token))
        with self.assertRaises(BlendAuthenticationError):
            auth.authorized_configs("Bearer " + self.login_token)
        self.assertEqual({field: self.config[field] for field in values}, values)
        # Revocation does not silently disable the separate legacy credential.
        self.assertIn(self.config, auth.authorized_configs("Bearer " + self.token))
        replacement = self.config._blend_issue_login_token()
        self.assertNotEqual(replacement, self.login_token)
        self.assertIn(self.config, auth.authorized_configs("Bearer " + replacement))

    def test_multi_branch_revocation_preserves_other_branch_using_same_login(self):
        selected = self.config | self._branch()
        untouched = self._branch()
        all_configs = selected | untouched
        shared_token = all_configs._blend_issue_login_token()
        untouched_before = self._snapshot(untouched)
        self.assertTrue(selected.with_user(self.administrator).action_revoke_blend_login_token())
        self.assertTrue(all(value == (False, False) for value in self._snapshot(selected).values()))
        self.assertEqual(self._snapshot(untouched), untouched_before)
        self.assertEqual(BlendAuthService(self.env).authorized_configs("Bearer " + shared_token), untouched)

    def test_record_rule_blocks_administrator_and_mixed_batch_without_partial_revocation(self):
        restricted = self._branch()
        restricted._blend_issue_login_token()
        self.env["ir.rule"].create({
            "name": "Test protected Blend branch", "model_id": self.env["ir.model"]._get_id("pos.config"),
            "domain_force": "[('id', '!=', %s)]" % restricted.id,
            "perm_read": False, "perm_write": True, "perm_create": False, "perm_unlink": False,
        })
        self.assertTrue(restricted.with_user(self.administrator).has_access("read"))
        self.assertFalse(restricted.with_user(self.administrator).has_access("write"))
        self._assert_denied_unchanged(restricted, self.administrator)
        self._assert_denied_unchanged(self.config | restricted, self.administrator)

    def test_model_acl_is_required_even_for_settings_administrator(self):
        # Local test transaction only: remove write grants, retaining read access.
        self.env["ir.model.access"].search([
            ("model_id.model", "=", "pos.config"), ("perm_write", "=", True),
        ]).write({"perm_write": False})
        self.assertFalse(self.config.with_user(self.administrator).has_access("write"))
        self._assert_denied_unchanged(self.config, self.administrator)

    def test_foreign_company_is_denied_without_partial_revocation(self):
        company = self.env["res.company"].create({"name": "Token revocation foreign company"})
        foreign = self._branch(company)
        foreign._blend_issue_login_token()
        self._assert_denied_unchanged(foreign, self.administrator)
        self._assert_denied_unchanged(self.config | foreign, self.administrator)

    def test_allowed_company_must_be_selected_before_revocation(self):
        company = self.env["res.company"].create({"name": "Token revocation second company"})
        foreign = self._branch(company)
        foreign._blend_issue_login_token()
        companies = self.env.company | company
        self.administrator.company_ids = [Command.set(companies.ids)]
        selected = self.config | foreign
        self._assert_denied_unchanged(selected, self.administrator)
        target = selected.with_user(self.administrator).with_context(allowed_company_ids=companies.ids)
        self.assertTrue(target.action_revoke_blend_login_token())
        self.assertTrue(all(value == (False, False) for value in self._snapshot(selected).values()))

    def test_repeated_authorized_revocation_is_harmless(self):
        target = self.config.with_user(self.administrator)
        self.assertTrue(target.action_revoke_blend_login_token())
        self.assertTrue(target.action_revoke_blend_login_token())
        self.assertEqual(self._snapshot(self.config)[self.config.id], (False, False))
