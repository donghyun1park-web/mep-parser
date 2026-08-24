"""폐합 통과까지 자동 연장 실행 회귀 테스트.

고정 endTime 으로 한 번만 돌리면 "계산은 끝났는데 물리적으로는 미완"인 결과가
리포트에 실린다(2026-08 사고: 1000회에서 멈춰 폐합 158%).
연장량은 임의 배수가 아니라 **관측된 잔차 감쇠율**에서 계산해야 한다.
"""
import os
import tempfile
import unittest
from unittest import mock
import json

import cfd_run


CONTROL_DICT = """FoamFile { version 2.0; format ascii; class dictionary; object controlDict; }
application     buoyantBoussinesqSimpleFoam;
startFrom       latestTime;
stopAt          endTime;
endTime         1000;
deltaT          1;
"""


class ControlDictEndTime(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "system"))
        with open(os.path.join(self.tmp, "system", "controlDict"), "w",
                  encoding="utf-8") as f:
            f.write(CONTROL_DICT)

    def test_reads_current_end_time(self):
        value, _, _ = cfd_run._control_dict_end_time(self.tmp)
        self.assertEqual(value, 1000.0)

    def test_writes_new_end_time_without_touching_rest(self):
        cfd_run._set_control_dict_end_time(self.tmp, 5000)
        value, text, _ = cfd_run._control_dict_end_time(self.tmp)
        self.assertEqual(value, 5000.0)
        self.assertIn("startFrom       latestTime;", text)   # 이어서 실행 유지
        self.assertIn("deltaT          1;", text)
        self.assertEqual(text.count("endTime"), 2)           # stopAt endTime 은 그대로


class AutoExtendLoop(unittest.TestCase):
    """run_until_closed 의 판단 흐름 — 실제 솔버 없이 상태만 갈아끼워 검증."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "system"))
        with open(os.path.join(self.tmp, "system", "controlDict"), "w",
                  encoding="utf-8") as f:
            f.write(CONTROL_DICT)
        self.runs = []

    def _fake_run(self, statuses):
        """run_case 를 가짜로 대체하고, 라운드마다 지정한 closure_status 를 돌려준다."""
        def fake_run_case(case, name=None, keep_mesh=False, progress_cb=None,
                          restart_from_latest=False):
            self.runs.append({"restart": restart_from_latest})
            return {"ok": True, "error": None, "case": case}
        seq = iter(statuses)
        return (mock.patch.object(cfd_run, "run_case", fake_run_case),
                mock.patch.object(cfd_run, "closure_status", lambda c: next(seq)))

    def test_stops_immediately_when_closure_passes(self):
        st = [{"closure_pct": 99.0, "citable": True, "need_iters": 0,
               "n_iters": 1200, "stalled": False}]
        p1, p2 = self._fake_run(st)
        with p1, p2:
            r = cfd_run.run_until_closed(self.tmp, progress_cb=lambda s: None)
        self.assertTrue(r["citable"])
        self.assertEqual(r["rounds"], 1)
        self.assertEqual(len(self.runs), 1)
        # 통과했으면 endTime 을 건드리지 않는다
        self.assertEqual(cfd_run._control_dict_end_time(self.tmp)[0], 1000.0)

    def test_extends_by_measured_residual_need_then_passes(self):
        st = [{"closure_pct": 158.0, "citable": False, "need_iters": 3500,
               "n_iters": 1000, "stalled": False},
              {"closure_pct": 101.0, "citable": True, "need_iters": 0,
               "n_iters": 4500, "stalled": False}]
        p1, p2 = self._fake_run(st)
        with p1, p2:
            r = cfd_run.run_until_closed(self.tmp, progress_cb=lambda s: None)
        self.assertTrue(r["citable"])
        self.assertEqual(r["rounds"], 2)
        # 연장량 = 관측된 필요 반복수(임의 배수가 아님)
        self.assertEqual(cfd_run._control_dict_end_time(self.tmp)[0], 4500.0)
        # 2라운드는 이어서 실행(restart)이어야 한다 — 처음부터 다시 돌리면 낭비
        self.assertEqual([x["restart"] for x in self.runs], [False, True])

    def test_stalled_residual_stops_instead_of_burning_cpu(self):
        st = [{"closure_pct": 60.0, "citable": False, "need_iters": None,
               "n_iters": 1000, "stalled": True}]
        p1, p2 = self._fake_run(st)
        with p1, p2:
            r = cfd_run.run_until_closed(self.tmp, progress_cb=lambda s: None)
        self.assertTrue(r.get("stall"))
        self.assertEqual(len(self.runs), 1)
        self.assertEqual(cfd_run._control_dict_end_time(self.tmp)[0], 1000.0)

    def test_respects_total_iteration_cap(self):
        st = [{"closure_pct": 158.0, "citable": False, "need_iters": 99999,
               "n_iters": 1000, "stalled": False},
              {"closure_pct": 140.0, "citable": False, "need_iters": 99999,
               "n_iters": 2000, "stalled": False}]
        p1, p2 = self._fake_run(st)
        with p1, p2:
            r = cfd_run.run_until_closed(self.tmp, progress_cb=lambda s: None,
                                         total_cap=3000)
        self.assertLessEqual(cfd_run._control_dict_end_time(self.tmp)[0], 3000.0)
        self.assertFalse(r["citable"])

    def test_round_limit_is_honoured(self):
        st = [{"closure_pct": 158.0 - i, "citable": False, "need_iters": 500,
               "n_iters": 1000 + i, "stalled": False} for i in range(6)]
        p1, p2 = self._fake_run(st)
        with p1, p2:
            r = cfd_run.run_until_closed(self.tmp, progress_cb=lambda s: None,
                                         max_rounds=3)
        self.assertEqual(r["rounds"], 3)
        self.assertEqual(len(self.runs), 3)

    def test_failed_run_aborts_without_extending(self):
        def fail_run(case, name=None, keep_mesh=False, progress_cb=None,
                     restart_from_latest=False):
            self.runs.append({"restart": restart_from_latest})
            return {"ok": False, "error": "솔버 실패", "case": case}
        with mock.patch.object(cfd_run, "run_case", fail_run):
            r = cfd_run.run_until_closed(self.tmp, progress_cb=lambda s: None)
        self.assertFalse(r["ok"])
        self.assertEqual(len(self.runs), 1)
        self.assertEqual(cfd_run._control_dict_end_time(self.tmp)[0], 1000.0)

    def test_no_closure_metric_does_not_loop_forever(self):
        """폐합을 계산할 수 없는 케이스(발열 없음 등)는 연장 대상이 아니다."""
        st = [{"closure_pct": None, "citable": None, "need_iters": 0,
               "n_iters": 800, "stalled": False}]
        p1, p2 = self._fake_run(st)
        with p1, p2:
            r = cfd_run.run_until_closed(self.tmp, progress_cb=lambda s: None)
        self.assertEqual(len(self.runs), 1)
        self.assertTrue(r["ok"])

    def test_closure_status_does_not_require_dashboard_field_metrics(self):
        """Auto-extension uses residual/energy evidence, not display-only T/U fields."""
        with open(os.path.join(self.tmp, "cfd_case_meta.json"), "w", encoding="utf-8") as handle:
            json.dump({"heat": {"mode": "volume", "power_w": 1000.0}}, handle)
        with open(os.path.join(self.tmp, "log.buoyantBoussinesqSimpleFoam"), "w",
                  encoding="utf-8") as handle:
            handle.write("End\n")
        parsed = {
            "crashed": False,
            "n_iters": 1000,
            "continuity_global": [(1000, 1e-7)],
            "residuals": {
                field: [1e-4]
                for field in ("Ux", "Uy", "Uz", "p_rgh", "T", "k", "epsilon")
            },
        }
        with mock.patch("cfd_report.parse_log", return_value=parsed), \
             mock.patch("cfd_report.energy_closure", return_value={
                 "closure_pct": 99.0, "closure_osc": 0.0, "mass_err_pct": 0.1,
             }):
            result = cfd_run.closure_status(self.tmp)

        self.assertTrue(result["closure_ready"])
        self.assertTrue(result["citable"])
        self.assertEqual(result["auto_extend_blockers"], [])


if __name__ == "__main__":
    unittest.main()
