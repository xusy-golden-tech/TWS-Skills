"""Store-layer exception classes.

All Store-layer exceptions inherit from StoreError, allowing callers to
catch all store errors with a single ``except StoreError`` clause.
"""


class StoreError(Exception):
    """Base class for all Store-layer exceptions."""


class StoreClosedError(StoreError):
    """Raised when calling methods on a closed Store."""


class TransactionError(StoreError):
    """Raised when transaction state is incorrect.

    Examples: calling commit/rollback without an active transaction,
    or attempting a nested transaction.
    """


class NodeNotFoundError(StoreError):
    """Raised when a required node is not found.

    Note: regular queries return None for missing nodes;
    this exception is for cases where strict checking is required.
    """


class EdgeNotFoundError(StoreError):
    """Raised when an edge is not found."""


class SchemaVersionError(StoreError):
    """Raised when the database schema version does not match what is expected."""


class MigrationError(StoreError):
    """Raised when a database migration fails."""
