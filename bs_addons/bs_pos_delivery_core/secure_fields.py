"""Encrypted ORM fields used by delivery connector credentials."""

import base64
import hashlib
import logging
import os
import re

from cryptography.fernet import Fernet, InvalidToken

from odoo import _, fields
from odoo.exceptions import ValidationError
from odoo.tools import SQL


_logger = logging.getLogger(__name__)
_CIPHERTEXT_PREFIX = "bsenc:v1:"
_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def _master_key_material(env):
    """Prefer an external secret-manager key, with a stable DB fallback."""
    configured = (os.getenv("BS_DELIVERY_MASTER_KEY") or "").strip()
    if configured:
        return configured
    return (
        env["ir.config_parameter"]
        .sudo()
        .get_param("database.secret", "")
        .strip()
    )


def _fernet(env):
    material = _master_key_material(env)
    if not material:
        raise ValidationError(
            _(
                "Delivery credential encryption is unavailable. Configure "
                "BS_DELIVERY_MASTER_KEY and restart Odoo."
            )
        )
    key = base64.urlsafe_b64encode(hashlib.sha256(material.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_value(env, value):
    value = str(value or "")
    if not value:
        return False
    if value.startswith(_CIPHERTEXT_PREFIX):
        value = decrypt_value(env, value)
    token = _fernet(env).encrypt(value.encode("utf-8")).decode("ascii")
    return "%s%s" % (_CIPHERTEXT_PREFIX, token)


def decrypt_value(env, value):
    value = str(value or "")
    if not value or not value.startswith(_CIPHERTEXT_PREFIX):
        # Compatibility with rows written before the encryption migration.
        return value or False
    token = value[len(_CIPHERTEXT_PREFIX) :]
    try:
        return _fernet(env).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError, ValueError) as error:
        _logger.error("Unable to decrypt a delivery credential", exc_info=True)
        raise ValidationError(
            _(
                "A delivery credential cannot be decrypted. Restore the "
                "BS_DELIVERY_MASTER_KEY used when it was saved."
            )
        ) from error


class EncryptedChar(fields.Char):
    """Expose plaintext to Python while persisting only ciphertext."""

    def convert_to_cache(self, value, record, validate=True):
        value = super().convert_to_cache(value, record, validate=validate)
        if value and str(value).startswith(_CIPHERTEXT_PREFIX):
            value = decrypt_value(record.env, value)
        return value

    def convert_to_record(self, value, record):
        """Decrypt values fetched directly from a database column.

        Odoo 19's ``_fetch_query`` inserts stored column values directly into
        the field cache.  It deliberately does not call ``convert_to_cache``
        for those values, so decrypting only in ``convert_to_cache`` works
        until the initial create/write cache is invalidated and then exposes
        the ciphertext to Python.  ``convert_to_record`` is the supported
        cache-to-record boundary and therefore must handle both plaintext
        values already in cache and ciphertext loaded from PostgreSQL.
        """
        value = super().convert_to_record(value, record)
        if value and str(value).startswith(_CIPHERTEXT_PREFIX):
            return decrypt_value(record.env, value)
        return value

    def convert_to_column(self, value, record, values=None, validate=True):
        value = super().convert_to_cache(value, record, validate=validate)
        # Preserve Char's distinction between '' and NULL. Domain compilation
        # converts the empty-string sentinel too; converting it to NULL makes
        # e.g. ('token', '!=', False) become NOT IN (NULL), matching no records.
        return encrypt_value(record.env, value) if value else value


def encrypt_legacy_columns(env, table, column_names):
    """Rewrite legacy cleartext values during an addon upgrade."""
    if not _IDENTIFIER_RE.fullmatch(table):
        raise ValueError("Unsafe SQL table identifier")
    for column_name in column_names:
        if not _IDENTIFIER_RE.fullmatch(column_name):
            raise ValueError("Unsafe SQL column identifier")
        quoted_table = SQL.identifier(table)
        quoted_column = SQL.identifier(column_name)
        env.cr.execute(
            SQL(
                "SELECT id, %s FROM %s "
                "WHERE %s IS NOT NULL AND %s != '' "
                "AND %s NOT LIKE %s",
                quoted_column,
                quoted_table,
                quoted_column,
                quoted_column,
                quoted_column,
                _CIPHERTEXT_PREFIX + "%",
            ),
        )
        for record_id, cleartext in env.cr.fetchall():
            env.cr.execute(
                SQL(
                    "UPDATE %s SET %s = %s WHERE id = %s",
                    quoted_table,
                    quoted_column,
                    encrypt_value(env, cleartext),
                    record_id,
                ),
            )
