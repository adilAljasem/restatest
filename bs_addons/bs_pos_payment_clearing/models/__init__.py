# The grouping extension must be registered after the legacy routing classes.
from . import (  # noqa: I001
    account_bank_statement_line,
    account_move,
    account_payment,
    pos_config,
    pos_payment_method,
    res_partner,
    pos_session,
    grouped_collection,
)
