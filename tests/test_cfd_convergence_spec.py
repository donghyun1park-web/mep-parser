import unittest

import cfd_convergence_spec
import cfd_report
import cfd_result_gate


class ConvergenceSpecSingleSourceTests(unittest.TestCase):
    def test_result_gate_imports_screening_trust_limits_not_a_copy(self):
        self.assertIs(
            cfd_result_gate.RESIDUAL_LIMITS,
            cfd_convergence_spec.SCREENING_TRUST_RESIDUAL_LIMITS,
        )

    def test_report_imports_forecast_targets_not_a_copy(self):
        self.assertIs(
            cfd_report.CONVERGENCE_TARGETS,
            cfd_convergence_spec.FORECAST_TARGET_RESIDUALS,
        )

    def test_forecast_targets_are_not_looser_than_screening_trust_limits(self):
        # 예측 목표(수렴 완료)가 신뢰 임계값(스크리닝 통과)보다 느슨해지면
        # "다 됐다고 예측했는데 신뢰 게이트는 여전히 막는다"는 모순이 생긴다.
        shared = set(cfd_convergence_spec.FORECAST_TARGET_RESIDUALS) & set(
            cfd_convergence_spec.SCREENING_TRUST_RESIDUAL_LIMITS)
        self.assertTrue(shared)
        for field in shared:
            forecast = cfd_convergence_spec.FORECAST_TARGET_RESIDUALS[field]
            trust = cfd_convergence_spec.SCREENING_TRUST_RESIDUAL_LIMITS[field]
            self.assertLessEqual(
                forecast, trust,
                f"{field}: forecast target {forecast} must not exceed "
                f"screening trust limit {trust}")


if __name__ == "__main__":
    unittest.main()
