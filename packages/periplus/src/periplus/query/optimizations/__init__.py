"""Explicit ordered alternatives. First applied/deferred pass wins; no rewrite chaining."""

from periplus.query.models import QueryMode
from periplus.query.optimizations.base import OptimizationPass
from periplus.query.optimizations.element_text import ELEMENT_TEXT

STABLE_PASSES: tuple[OptimizationPass, ...] = ()
EXPERIMENTAL_PASSES = (ELEMENT_TEXT,)


def passes_for_mode(mode: QueryMode) -> tuple[OptimizationPass, ...]:
    if mode == QueryMode.STABLE:
        return STABLE_PASSES
    if mode == QueryMode.EXPERIMENTAL:
        return (*STABLE_PASSES, *EXPERIMENTAL_PASSES)
    raise ValueError(f"Unsupported query mode: {mode}")
