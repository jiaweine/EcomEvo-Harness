"""Compatibility import for the production Harness evolution optimizer.

The implementation lives in :mod:`ecomevo.runtime.harness_optimizer` so the optimizer can
change without breaking the public import path used by runtime integrations and tests.
"""

from .harness_optimizer import HarnessComponent, HarnessEvolutionOptimizer

__all__ = ["HarnessComponent", "HarnessEvolutionOptimizer"]
