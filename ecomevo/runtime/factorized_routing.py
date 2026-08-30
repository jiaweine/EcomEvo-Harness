from __future__ import annotations

import math
from typing import Any

from .adaptive_routing import AdaptiveRoutingStore


class FactorizedAdaptiveRoutingStore(AdaptiveRoutingStore):
    """Built-in routing scorer that avoids materializing a full precision inverse.

    Routing precision matrices are symmetric positive-definite by construction: a
    positive diagonal prior is combined with decayed prior-relative state and outer
    products of observed feature vectors. Cholesky factorization therefore gives the
    posterior mean and quadratic uncertainty forms directly, with the legacy generic
    inverse retained as a defensive fallback for unexpected/corrupt state.

    This class is intentionally separate from ``AdaptiveRoutingStore`` so the public
    store/plugin behavior and compatibility path remain unchanged.
    """

    _FACTOR_EPSILON = 1e-12

    @classmethod
    def _cholesky_factor(cls, matrix: list[list[float]]) -> list[list[float]]:
        n = len(matrix)
        if n == 0 or any(len(row) != n for row in matrix):
            raise ValueError("routing precision matrix must be non-empty and square")

        lower = [[0.0] * n for _ in range(n)]
        for row in range(n):
            for col in range(row + 1):
                subtotal = 0.0
                for k in range(col):
                    subtotal += lower[row][k] * lower[col][k]
                value = float(matrix[row][col]) - subtotal
                if row == col:
                    if value <= cls._FACTOR_EPSILON or not math.isfinite(value):
                        raise ValueError("routing precision matrix is not positive definite")
                    lower[row][col] = math.sqrt(value)
                else:
                    diagonal = lower[col][col]
                    if abs(diagonal) <= cls._FACTOR_EPSILON:
                        raise ValueError("routing precision factor has a zero diagonal")
                    lower[row][col] = value / diagonal
        return lower

    @staticmethod
    def _solve_lower(lower: list[list[float]], vector: list[float]) -> list[float]:
        n = len(lower)
        if len(vector) != n:
            raise ValueError("routing solve dimension mismatch")
        result = [0.0] * n
        for row in range(n):
            subtotal = 0.0
            for col in range(row):
                subtotal += lower[row][col] * result[col]
            result[row] = (float(vector[row]) - subtotal) / lower[row][row]
        return result

    @staticmethod
    def _solve_upper_from_lower(
        lower: list[list[float]],
        vector: list[float],
    ) -> list[float]:
        n = len(lower)
        if len(vector) != n:
            raise ValueError("routing solve dimension mismatch")
        result = [0.0] * n
        for row in range(n - 1, -1, -1):
            subtotal = 0.0
            for col in range(row + 1, n):
                subtotal += lower[col][row] * result[col]
            result[row] = (float(vector[row]) - subtotal) / lower[row][row]
        return result

    @classmethod
    def _solve_precision(
        cls,
        lower: list[list[float]],
        vector: list[float],
    ) -> list[float]:
        return cls._solve_upper_from_lower(lower, cls._solve_lower(lower, vector))

    def _posterior_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        try:
            factor = self._cholesky_factor(row["a"])
            mean = self._solve_precision(factor, [float(value) for value in row["b"]])
        except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError):
            # Preserve the generic compatibility behavior for malformed or legacy state.
            return super()._posterior_from_row(row)
        return {**row, "factor": factor, "mean": mean}

    def _posterior_variance(
        self,
        posterior: dict[str, Any],
        vector: list[float],
    ) -> float:
        factor = posterior.get("factor")
        if isinstance(factor, list):
            # For A = L L^T, x^T A^-1 x = ||L^-1 x||^2. No full inverse needed.
            transformed = self._solve_lower(factor, vector)
            return max(0.0, sum(value * value for value in transformed))

        inverse = posterior.get("inverse")
        if not isinstance(inverse, list):  # pragma: no cover - defensive corrupt state
            raise ValueError("routing posterior has neither factor nor inverse")
        projected = self._matvec(inverse, vector)
        return max(0.0, self._dot(vector, projected))

    def score_prepared(
        self,
        vector: list[float],
        prepared: dict[str, Any],
    ) -> dict[str, float | int | str]:
        if len(vector) != self.dim:
            raise ValueError(
                f"routing feature dimension mismatch: {len(vector)} != {self.dim}"
            )
        global_p = prepared["global"]
        domain_p = prepared["domain"]
        tau = float(prepared["tau"])
        n = int(prepared["samples"])
        global_mean = self._dot(global_p["mean"], vector)
        domain_mean = self._dot(domain_p["mean"], vector)
        posterior_mean = (1.0 - tau) * global_mean + tau * domain_mean
        global_var = self._posterior_variance(global_p, vector)
        domain_var = self._posterior_variance(domain_p, vector)
        uncertainty = math.sqrt(
            max(
                0.0,
                (1.0 - tau) ** 2 * global_var + tau ** 2 * domain_var,
            )
        )
        prior_score = self._dot(self.PRIOR_MEAN, vector)
        optimistic = posterior_mean + float(prepared["beta"]) * uncertainty
        if n < 64:
            optimistic = max(
                prior_score - 0.70,
                min(prior_score + 0.70, optimistic),
            )
        activation = float(prepared["activation"])
        final = (1.0 - activation) * prior_score + activation * optimistic
        return {
            "score": float(final),
            "prior": float(prior_score),
            "posterior": float(posterior_mean),
            "uncertainty": float(uncertainty),
            "activation": activation,
            "samples": n,
            "mode": str(prepared["mode"]),
            "residual": float(prepared["residual"]),
        }
