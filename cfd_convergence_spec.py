"""Single source for the legacy structured-grid screening residual thresholds.

Two genuinely different numbers exist for the same field names, and previously
lived in two disconnected modules (``cfd_report.CONVERGENCE_TARGETS`` and
``cfd_result_gate.RESIDUAL_LIMITS``) with no cross-reference between them.
This module is now the only place either is defined; both call sites import
from here so a future edit cannot let them drift apart unnoticed.

They are *supposed* to differ, and must not be collapsed into one number:

- ``FORECAST_TARGET_RESIDUALS`` is the "fully converged" target used only by
  ``cfd_report.residual_decay_forecast`` to estimate how many more iterations
  are needed. It is not a pass/fail bar.
- ``SCREENING_TRUST_RESIDUAL_LIMITS`` is the practical trust bar used by
  ``cfd_result_gate.evaluate_screening_result``. It is deliberately looser,
  because it never acts alone: it is one of several independent checks
  (continuity, energy-closure percentage, mass balance) that must all agree
  before a screening result is trusted.

That layering is what the 2026-08 158%-closure incident was missing: the
legacy ``fvSolution`` ``residualControl`` block used to be the *only* signal
(loose ``T: 1e-2``), so the solver declared "converged" while the temperature
field was still ~4 K from equilibrium. Fixes since then: ``fvSolution``
tightened its own early-stop bar to match ``FORECAST_TARGET_RESIDUALS``
(1e-5) for non-opening cases, and opening-based cases (all current SGI
cases) strip ``residualControl`` entirely — measured early self-termination
before energy closure, see ``cfd_export._build_case_into``. For every case
type, energy closure (``cfd_result_gate.CLOSURE_OK``) is now an independent
check alongside residuals, so ``SCREENING_TRUST_RESIDUAL_LIMITS`` being
looser than the forecast target no longer reproduces that failure mode by
itself.
"""

from __future__ import annotations


FORECAST_TARGET_RESIDUALS = {
    "Ux": 1e-5, "Uy": 1e-5, "Uz": 1e-5, "T": 1e-5,
    "p_rgh": 1e-4, "k": 1e-4, "epsilon": 1e-4, "omega": 1e-4,
}

SCREENING_TRUST_RESIDUAL_LIMITS = {
    "Ux": 1e-3,
    "Uy": 1e-3,
    "Uz": 1e-3,
    "p_rgh": 1e-2,
    "T": 1e-2,
    "h": 1e-2,
    "k": 1e-2,
    "epsilon": 1e-2,
    "omega": 1e-2,
}
