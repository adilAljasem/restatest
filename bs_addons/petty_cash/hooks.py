# -*- coding: utf-8 -*-
"""Idempotent creation of the Petty Cash accounts and journal.

These three records used to live in ``data/data.xml``. As plain XML records
they are always *created*, so the module could not be installed on any database
that already owned an account with code ``10000000001`` / ``10000000011`` or a
journal with code ``PCSH``:

    psycopg2.errors.UniqueViolation: duplicate key value violates unique
    constraint "account_journal_code_company_uniq"
    DETAIL: Key (company_id, code)=(1, PCSH) already exists.

That happens on every re-install after an uninstall (the ``ir.model.data`` rows
are dropped but the journal and accounts survive), and on any database where
those codes are already in use.

This hook takes the opposite approach: look first, create only what is missing,
and never modify a record that was already there. Whatever it ends up with is
bound to the original external ids so ``env.ref('petty_cash.petty_cash_journal')``
keeps resolving for the rest of the module.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

MODULE = 'petty_cash'

PETTY_ACCOUNT_XMLID = 'petty_account'
SUSPENSE_ACCOUNT_XMLID = 'petty_suspense_account'
JOURNAL_XMLID = 'petty_cash_journal'

PETTY_ACCOUNT_CODE = '10000000001'
SUSPENSE_ACCOUNT_CODE = '10000000011'
JOURNAL_CODE = 'PCSH'


def _existing_ref(env, name):
    """Return the record behind petty_cash.<name>, or an empty recordset."""
    record = env.ref('%s.%s' % (MODULE, name), raise_if_not_found=False)
    return record if record and record.exists() else None


def _bind_xmlid(env, name, record, adopted):
    """Point petty_cash.<name> at ``record`` (noupdate, so upgrades never touch it)."""
    data = env['ir.model.data'].sudo().search([
        ('module', '=', MODULE),
        ('name', '=', name),
    ], limit=1)
    vals = {
        'module': MODULE,
        'name': name,
        'model': record._name,
        'res_id': record.id,
        'noupdate': True,
    }
    if data:
        data.write(vals)
    else:
        env['ir.model.data'].sudo().create(vals)
    _logger.info(
        "petty_cash: %s %s.%s -> %s (id %s)",
        'adopted existing' if adopted else 'created', MODULE, name,
        record.display_name, record.id,
    )


def _find_account(env, company, code):
    """Find an account by code in ``company``.

    In Odoo 19 ``account.account.code`` is company dependent, so the search has
    to run with the right company activated.
    """
    Account = env['account.account'].with_company(company).sudo()
    return Account.search([
        ('code', '=', code),
        ('company_ids', 'in', company.id),
    ], limit=1)


def _get_or_create_account(env, company, xmlid, code, name, account_type):
    existing = _existing_ref(env, xmlid)
    if existing:
        return existing

    account = _find_account(env, company, code)
    if account:
        _bind_xmlid(env, xmlid, account, adopted=True)
        return account

    account = env['account.account'].with_company(company).sudo().create({
        'name': name,
        'code': code,
        'account_type': account_type,
        'reconcile': True,
        'company_ids': [(6, 0, [company.id])],
    })
    _bind_xmlid(env, xmlid, account, adopted=False)
    return account


def _find_journal(env, company):
    """Find the journal this module should use, most specific match first."""
    Journal = env['account.journal'].sudo()
    # 1. A journal already flagged as the permanent petty cash journal.
    journal = Journal.search([
        ('petty_cash_checkbox', '=', True),
        ('company_id', '=', company.id),
    ], limit=1)
    if journal:
        return journal
    # 2. A journal that simply occupies the PCSH code (the collision case).
    return Journal.search([
        ('code', '=', JOURNAL_CODE),
        ('company_id', '=', company.id),
    ], limit=1)


def _get_or_create_journal(env, company, petty_account, suspense_account):
    existing = _existing_ref(env, JOURNAL_XMLID)
    if existing:
        return existing

    journal = _find_journal(env, company)
    if journal:
        # Adopt it as-is. Its name, accounts and any posted entries are left
        # exactly as the customer configured them.
        _bind_xmlid(env, JOURNAL_XMLID, journal, adopted=True)
        return journal

    journal = env['account.journal'].with_company(company).sudo().create({
        'name': 'Petty Cash',
        'code': JOURNAL_CODE,
        'type': 'cash',
        'company_id': company.id,
        'default_account_id': petty_account.id,
        'suspense_account_id': suspense_account.id,
        'show_on_dashboard': True,
    })
    _bind_xmlid(env, JOURNAL_XMLID, journal, adopted=False)
    return journal


def _has_chart_of_accounts(env, company):
    """True when ``company`` already has a chart of accounts loaded.

    Installing a chart template into a company wipes the accounts and journals
    that were there before it. So if this module is installed onto a company
    that has no chart yet, anything created here would be silently destroyed
    the moment the chart is loaded, leaving dead external ids behind. In that
    situation the safe thing is to create nothing at all.
    """
    if company.chart_template:
        return True
    return bool(env['account.account'].sudo().search_count(
        [('company_ids', 'in', company.id)]
    ))


def post_init_hook(env):
    """Create the Petty Cash accounts and journal only if they are missing."""
    if not isinstance(env, api.Environment):          # very old signature guard
        env = api.Environment(env, SUPERUSER_ID, {})

    company = env.ref('base.main_company', raise_if_not_found=False) or env.company
    if not company:
        _logger.warning("petty_cash: no company found, skipping default data.")
        return

    if not _has_chart_of_accounts(env, company):
        _logger.warning(
            "petty_cash: company %s has no chart of accounts yet, so the Petty "
            "Cash accounts and journal were not created - loading a chart "
            "template later would have deleted them. Load the chart of "
            "accounts, then tick 'Permanent Petty Cash' on the cash journal you "
            "want this module to use (Accounting > Configuration > Journals > "
            "Advanced Settings).",
            company.display_name,
        )
        return

    petty_account = _get_or_create_account(
        env, company, PETTY_ACCOUNT_XMLID, PETTY_ACCOUNT_CODE,
        'Petty Cash', 'asset_cash',
    )
    suspense_account = _get_or_create_account(
        env, company, SUSPENSE_ACCOUNT_XMLID, SUSPENSE_ACCOUNT_CODE,
        'Petty Cash Suspense', 'asset_current',
    )
    _get_or_create_journal(env, company, petty_account, suspense_account)
