"""Errors a caller can show to a person without a traceback."""


class ProofJsonError(Exception):
    """A predictable failure: bad input, a missing API key, or unusable model output."""
