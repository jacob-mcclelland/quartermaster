"""Minimal Jev client: typed decisions, logged, with order-swap debiasing.

Standard library only. It talks to the documented HTTP API directly and pins
the model version, so an SDK release cannot change behaviour underneath it.
"""

from .client import Jev, JevError, choice, noul, score

__all__ = ["Jev", "JevError", "noul", "choice", "score"]
