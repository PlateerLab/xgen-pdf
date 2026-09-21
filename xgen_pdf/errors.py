"""Exception types."""


class XgenPdfError(Exception):
    """Base class for engine errors."""


class FileDataError(XgenPdfError, RuntimeError):
    """The file is not a readable PDF."""


class PasswordError(XgenPdfError, RuntimeError):
    """The document is encrypted and no valid password was given."""


class EmptyFileError(FileDataError):
    """The input is empty."""
