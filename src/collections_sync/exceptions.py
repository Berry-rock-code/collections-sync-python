"""Custom exceptions for collections-sync robustness features."""


class LockTimeoutError(Exception):
    """Raised when the distributed sync lock cannot be acquired within timeout."""

    pass


class LockAcquireError(Exception):
    """Raised when the lock cell cannot be read or written due to API failure."""

    pass


class DataValidationError(Exception):
    """Raised when a row fails field-level validation before writing."""

    pass


class DataCorruptionError(Exception):
    """Raised when a post-write checksum verification fails."""

    pass
