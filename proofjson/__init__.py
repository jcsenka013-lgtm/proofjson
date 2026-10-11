"""A trust layer for LLM extraction."""

from proofjson.extract import ExtractionResult, extract
from proofjson.version import __version__

__all__ = ["ExtractionResult", "extract", "__version__"]
