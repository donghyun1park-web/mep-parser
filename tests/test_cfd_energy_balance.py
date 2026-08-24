"""정상상태 에너지수지 회귀 테스트.

배경(2026-08 실측 사고, 'SGI 전체 로비EHP 3대 기준' 케이스):
초기 T 를 300 K 로 고정 배포 → 1.5 ACH 저환기 방(공기교체 시간상수 ~40분)에서
1000 iteration 으로는 초기장이 안 빠짐 → 배기온도가 초기값 300 K 에 머물러
급기 289.15 K 와의 차 10.9 K 가 '배기 엔탈피'로 잡힘 → 에너지 폐합 158%
(= 나간 열이 넣은 열의 1.6배, 물리적으로 불가능)가 리포트에 그대로 실렸다.
평균온도 26.7 °C 는 사실상 초기값이었고 실제 평형은 약 23 °C 였다.

이 테스트는 그 사고의 수치를 그대로 고정해 재발을 막는다.
"""
import json
from pathlib import Path
import tempfile
import unittest

import cfd_export
import cfd_report


# 사고 케이스 실제 설정(cfd_case_meta.json 발췌)
ACCIDENT_CFG = {
    "name": "lobby",
    "room": {"L": 27.6, "W": 15.9, "H": 10.0},
    "mesh": {"cell": 0.15},
    "inlet": {"wall": "x0", "T": 289.15},
    "heat": {"power_kw": 15.5},
    "init": {"T": 300.0},          # ← 사고 당시의 고정 초기값
    "endTime": 1000,
}
# 급기 15개소 총 6660 CMH, 배기 15개소. 면적은 격자 스냅 후 실측값.
ACCIDENT_PATCHES = (
    [{"name": f"sup{i}", "role": "supply", "cmh": 444.0, "area": 0.045,
      "T": 289.15, "U": [0.0, 0.0, -2.7407]} for i in range(15)]
    + [{"name": f"exh{i}", "role": "exhaust", "cmh": 444.0, "area": 0.045}
       for i in range(15)]
)


class EquilibriumTemperature(unittest.TestCase):
    """해석적 평형온도가 리뷰어 손계산과 일치하는가."""

    def test_matches_hand_calculation(self):
        T_eq, info = cfd_export.equilibrium_temperature(ACCIDENT_CFG, ACCIDENT_PATCHES)
        self.assertAlmostEqual(info["vdot_m3s"], 6660 / 3600.0, places=6)
        self.assertAlmostEqual(info["mcp_w_per_k"], 2231.1, delta=0.5)   # ρcp·V̇
        self.assertAlmostEqual(info["delta_T_K"], 6.95, delta=0.02)      # Q/(ṁcp)
        self.assertAlmostEqual(T_eq - 273.15, 22.95, delta=0.05)         # ≈ 23 °C
        self.assertAlmostEqual(info["ach"], 1.52, delta=0.02)
        # 공기교체 시간상수 ≈ 40분 — 저환기라 초기장이 늦게 빠진다는 근거
        self.assertAlmostEqual(info["flush_time_s"] / 60.0, 39.5, delta=0.5)

    def test_returns_none_without_heat_or_supply(self):
        cfg = dict(ACCIDENT_CFG, heat={})
        T_eq, info = cfd_export.equilibrium_temperature(cfg, ACCIDENT_PATCHES)
        self.assertIsNone(T_eq)
        self.assertIn("reason", info)

    def test_wall_supply_mode_uses_area_times_velocity(self):
        """개구부 패치가 없는 벽 급기 모드도 유량을 낼 수 있어야 한다."""
        cfg = {"room": {"L": 4.0, "W": 3.0, "H": 2.5}, "mesh": {"cell": 0.5},
               "inlet": {"wall": "x0", "U": [0.3, 0, 0], "T": 293.15},
               "heat": {"power_kw": 5.0}}
        vdot = cfd_export.supply_flow_m3s(cfg, None)
        self.assertAlmostEqual(vdot, 3.0 * 2.5 * 0.3, places=6)   # W·H·|U|
        T_eq, _ = cfd_export.equilibrium_temperature(cfg, None)
        self.assertIsNotNone(T_eq)


class InitialFieldUsesEquilibrium(unittest.TestCase):
    """0/T 초기장이 고정 300 K 가 아니라 평형온도여야 한다(사고 재발 방지)."""

    def test_resolve_init_T_overrides_configured_value(self):
        T_init, note = cfd_export.resolve_init_T(ACCIDENT_CFG, ACCIDENT_PATCHES)
        self.assertAlmostEqual(T_init - 273.15, 22.95, delta=0.05)
        self.assertEqual(note["source"], "equilibrium")
        self.assertTrue(note["overridden"])
        self.assertEqual(note["configured_T_K"], 300.0)

    def test_generated_T_field_has_no_hardcoded_300(self):
        files = cfd_export.gen_0_openings(ACCIDENT_CFG, ACCIDENT_PATCHES)
        T = files["T"]
        self.assertIn("internalField uniform 296.09", T)
        self.assertNotIn("internalField uniform 300", T)
        # 배기 역류 시 주입되는 공기온도도 평형온도여야 한다.
        # 300 K 로 두면 실내(296 K)보다 뜨거운 공기가 들어와 열이 '생긴다'.
        self.assertIn("exh0 { type inletOutlet; inletValue uniform 296.09", T)
        self.assertNotIn("inletValue uniform 300", T)

    def test_falls_back_to_config_when_physics_unknown(self):
        cfg = dict(ACCIDENT_CFG, heat={})
        T_init, note = cfd_export.resolve_init_T(cfg, ACCIDENT_PATCHES)
        self.assertEqual(T_init, 300.0)
        self.assertEqual(note["source"], "config")


class ConfirmedEquipmentHeatContract(unittest.TestCase):
    def test_equilibrium_uses_applied_convective_not_nameplate_heat(self):
        cfg = {
            "room": {"L": 4.0, "W": 3.0, "H": 2.5},
            "mesh": {"cell": 0.5},
            "inlet": {"T": 293.15},
            "heat": {},
            "obstacles": [{
                "kind": "equipment", "bbox": [1.5, 1.0, 2.0, 1.5],
                "h": 1.0, "kw": 5.0, "convective_fraction": 0.8,
            }],
        }
        patches = [{"role": "supply", "cmh": 600.0, "T": 293.15}]

        T_eq, info = cfd_export.equilibrium_temperature(cfg, patches)

        self.assertEqual(info["power_w"], 4000.0)
        self.assertEqual(info["input_power_w"], 5000.0)
        self.assertAlmostEqual(T_eq, 293.15 + 4000.0 / (1206.0 * (600.0 / 3600.0)))

    def test_legacy_equipment_applies_only_confirmed_convective_fraction(self):
        cfg = {
            "room": {"L": 2.0, "W": 2.0, "H": 2.0},
            "mesh": {"cell": 0.5},
            "heat": {},
            "obstacles": [{
                "kind": "equipment",
                "bbox": [0.5, 0.5, 1.5, 1.5],
                "h": 1.0,
                "kw": 5.0,
                "convective_fraction": 0.8,
                "source_id": "DXF-EHP-01",
                "source_label": "EHP 실내기 1",
                "evidence": "equipment_schedule:M03-001",
                "source_type": "user_confirmed",
            }],
        }
        labels = cfd_export.solid_labels(
            cfg, {"nx": 4, "ny": 4, "nz": 4, "cells": 64}
        )

        self.assertEqual(labels["equip"][0][1], 4.0)
        contract = labels["equip_contract"][0]
        self.assertEqual(contract["source_id"], "DXF-EHP-01")
        self.assertEqual(contract["input_power_w"], 5000.0)
        self.assertEqual(contract["convective_power_w"], 4000.0)
        self.assertEqual(contract["radiative_fraction"], 0.2)
        self.assertEqual(contract["radiative_power_w"], 1000.0)
        self.assertEqual(contract["excluded_radiative_power_w"], 1000.0)
        self.assertEqual(contract["evidence"], "equipment_schedule:M03-001")
        self.assertIn(
            "injectionRateSuSp { T (3.31675",  # 4 kW / rhoCp
            cfd_export.gen_fvoptions_v3(cfg, labels),
        )

    def test_legacy_case_metadata_keeps_input_and_unmodelled_radiative_heat(self):
        cfg = {
            "name": "confirmed-heat",
            "room": {"L": 4.0, "W": 3.0, "H": 2.5},
            "mesh": {"cell": 0.5},
            "inlet": {"T": 293.15},
            "heat": {},
            "init": {"T": 293.15},
            "endTime": 10,
            "openings": [
                {"role": "supply", "type": "grille", "wall": "ceiling",
                 "cx": 1.0, "cy": 1.0, "w": 0.5, "h": 0.5,
                 "cmh": 300, "T": 293.15},
                {"role": "exhaust", "type": "grille", "wall": "ceiling",
                 "cx": 3.0, "cy": 2.0, "w": 0.5, "h": 0.5,
                 "cmh": 300},
            ],
            "obstacles": [{
                "kind": "equipment", "bbox": [1.5, 1.0, 2.0, 1.5],
                "h": 1.0, "kw": 5.0, "convective_fraction": 0.8,
                "source_id": "DXF-EHP-01", "source_label": "EHP 실내기 1",
                "evidence": "equipment_schedule:M03-001",
                "source_type": "user_confirmed",
            }],
        }
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp) / "case"
            cfd_export.build_case(cfg, case)
            meta = json.loads((case / "cfd_case_meta.json").read_text(encoding="utf-8"))

        self.assertEqual(meta["heat"]["input_power_w"], 5000.0)
        self.assertEqual(meta["heat"]["applied_convective_power_w"], 4000.0)
        self.assertEqual(meta["heat"]["radiative_power_w"], 1000.0)
        self.assertEqual(meta["heat"]["excluded_radiative_power_w"], 1000.0)
        self.assertEqual(meta["equip_zones"][0]["radiative_fraction"], 0.2)
        self.assertEqual(meta["equip_zones"][0]["source_id"], "DXF-EHP-01")
        self.assertEqual(meta["equip_zones"][0]["evidence"], "equipment_schedule:M03-001")

    def test_legacy_equipment_accepts_canonical_power_aliases(self):
        """V3a must not drop a reviewed heat load just because it is stored in W."""
        base = {
            "kind": "equipment", "bbox": [0.5, 0.5, 1.5, 1.5], "h": 1.0,
            "convective_fraction": 0.8,
            "source_id": "DXF-EHP-01", "source_label": "EHP \uc2e4\ub0b4\uae30 1",
            "evidence": "equipment_schedule:M03-001",
            "source_type": "user_confirmed",
        }
        for field, value in (("input_power_w", 5000.0), ("power_kw", 5.0)):
            with self.subTest(field=field):
                summary = cfd_export._equipment_heat_summary({
                    "obstacles": [{**base, field: value}],
                })

                self.assertEqual(summary["source_count"], 1)
                self.assertEqual(summary["input_power_w"], 5000.0)
                self.assertEqual(summary["applied_convective_power_w"], 4000.0)
                self.assertEqual(summary["excluded_radiative_power_w"], 1000.0)

    def test_legacy_canonical_power_alias_blocks_global_heat_double_count(self):
        """A W-based reviewed equipment load and global heat must be mutually exclusive."""
        cfg = {
            "name": "alias-double-count",
            "room": {"L": 4.0, "W": 3.0, "H": 2.5},
            "mesh": {"cell": 0.5},
            "inlet": {"T": 293.15},
            "heat": {"power_kw": 1.0},
            "init": {"T": 293.15},
            "endTime": 10,
            "openings": [
                {"role": "supply", "type": "grille", "wall": "ceiling",
                 "cx": 1.0, "cy": 1.0, "w": 0.5, "h": 0.5,
                 "cmh": 300, "T": 293.15},
                {"role": "exhaust", "type": "grille", "wall": "ceiling",
                 "cx": 3.0, "cy": 2.0, "w": 0.5, "h": 0.5,
                 "cmh": 300},
            ],
            "obstacles": [{
                "kind": "equipment", "bbox": [1.5, 1.0, 2.0, 1.5],
                "h": 1.0, "input_power_w": 5000.0, "convective_fraction": 0.8,
                "source_id": "DXF-EHP-01", "source_label": "EHP \uc2e4\ub0b4\uae30 1",
                "evidence": "equipment_schedule:M03-001",
                "source_type": "user_confirmed",
            }],
        }
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(SystemExit, "obstacles"):
                cfd_export.build_case(cfg, Path(tmp) / "case")

    def test_legacy_rejects_duplicate_positive_heat_source_identity(self):
        source = {
            "kind": "equipment", "bbox": [0.5, 0.5, 1.5, 1.5], "h": 1.0,
            "kw": 5.0, "convective_fraction": 0.8,
            "source_id": "DXF-EHP-01", "source_label": "EHP 실내기 1",
            "source_ref": {"handle": "EHP-A1", "layer": "DVM_INDOOR"},
            "evidence": "equipment_schedule:M03-001",
            "source_type": "user_confirmed",
        }
        with self.assertRaisesRegex(SystemExit, "duplicate source_id"):
            cfd_export._equipment_heat_summary({"obstacles": [source, dict(source)]})

    def test_legacy_missing_type_cad_identity_cannot_become_heat_input(self):
        """A historic V3a default must not promote a CAD-backed load to manual."""
        base = {
            "kind": "equipment", "bbox": [0.5, 0.5, 1.5, 1.5], "h": 1.0,
            "kw": 5.0, "convective_fraction": 0.8,
            "source_id": "equipment_DXF_EHP_01", "source_label": "EHP 1",
            "evidence": "equipment_schedule:M03-001",
        }
        identities = (
            {"source_ref": {"handle": "EHP-A1", "layer": "DVM_INDOOR"}},
            {"source_ref": {"source_handle": "EHP-A1", "layer": "DVM_INDOOR"}},
            {"source_ref": {"handles": ["EHP-A1"], "layer": "DVM_INDOOR"}},
            {"source_handle": "EHP-A1"},
            {"source_handles": ["EHP-A1"]},
        )
        for identity in identities:
            with self.subTest(identity=identity):
                with self.assertRaisesRegex(SystemExit, "source_type|DXF|CAD"):
                    cfd_export._equipment_heat_summary({
                        "obstacles": [{**base, **identity}],
                    })

    def test_legacy_manual_without_cad_identity_remains_supported(self):
        """A real pre-provenance manual input still has the legacy fallback."""
        summary = cfd_export._equipment_heat_summary({
            "obstacles": [{
                "kind": "equipment", "bbox": [0.5, 0.5, 1.5, 1.5], "h": 1.0,
                "kw": 5.0, "convective_fraction": 0.8,
                "source_id": "manual_heat_1", "source_label": "manual heat 1",
            }],
        })

        self.assertEqual(summary["source_count"], 1)
        self.assertEqual(summary["input_power_w"], 5000.0)
        self.assertEqual(summary["sources"][0]["source_type"], "legacy_manual_input")

    def test_legacy_explicit_dxf_type_cannot_become_heat_input(self):
        """The V3a adapter itself must refuse raw DXF heat, not merely the UI."""
        with self.assertRaisesRegex(SystemExit, "DXF"):
            cfd_export._equipment_heat_summary({
                "obstacles": [{
                    "kind": "equipment", "bbox": [0.5, 0.5, 1.5, 1.5], "h": 1.0,
                    "kw": 5.0, "convective_fraction": 0.8,
                    "source_id": "equipment_DXF_EHP_01", "source_label": "EHP 1",
                    "source_type": "dxf_detected",
                    "source_ref": {"handle": "EHP-A1", "layer": "DVM_INDOOR"},
                    "evidence": "equipment_schedule:M03-001",
                }],
            })


class ClosureBlocksCitation(unittest.TestCase):
    """폐합율이 물리적으로 불가능하면 결과 인용을 차단해야 한다."""

    def _parsed(self, residual=1e-4):
        return {"crashed": False, "continuity_global": [(1000, 1e-7)],
                "residuals": {field: [1e-1, residual] for field in (
                    "Ux", "Uy", "Uz", "p_rgh", "T", "k", "epsilon"
                )}}

    def test_accident_closure_158_is_not_citable(self):
        t = cfd_report.result_trust(self._parsed(), {"closure_pct": 158.0})
        self.assertFalse(t["citable"])
        self.assertEqual(t["color"], "#c0392b")      # 적색 = 차단
        self.assertIn("인용 불가", t["badge"])
        self.assertTrue(any("158" in r for r in t["reasons"]))

    def test_starved_closure_also_blocked(self):
        t = cfd_report.result_trust(self._parsed(), {"closure_pct": 40.0})
        self.assertFalse(t["citable"])
        self.assertIn("축열", " ".join(t["reasons"]))

    def test_good_closure_is_citable(self):
        t = cfd_report.result_trust(
            self._parsed(), {"closure_pct": 99.0, "closure_osc": 2.0,
                             "mass_err_pct": 1.0, "T_avg_C": 25.0,
                             "T_max_C": 26.0, "U_max": 0.4})
        self.assertTrue(t["citable"])
        self.assertEqual(t["color"], "#1e8449")

    def test_mild_deviation_warns_but_does_not_hard_block(self):
        """95~110 밖이어도 하드 한계(75~125) 안이면 노란 미수렴 — 색으로 구분."""
        t = cfd_report.result_trust(self._parsed(), {"closure_pct": 118.0})
        self.assertFalse(t["citable"])
        self.assertEqual(t["color"], "#b9770e")

    def test_badge_wrapper_keeps_two_tuple_contract(self):
        badge, color = cfd_report.convergence_badge(
            self._parsed(), {"closure_pct": 158.0})
        self.assertIsInstance(badge, str)
        self.assertEqual(color, "#c0392b")


class ResidualForecast(unittest.TestCase):
    """'몇 회 더 돌려야 하나'를 숫자로 답할 수 있어야 한다."""

    def test_estimates_remaining_iterations_from_decay_rate(self):
        # 반복당 1% 감소, 마지막 잔차가 정확히 1e-3 인 시계열을 만든다.
        # 남은 반복 = ln(1e-3/1e-5) / -ln(0.99) ≈ 458회
        ser = [1e-3 / (0.99 ** (199 - i)) for i in range(200)]
        self.assertAlmostEqual(ser[-1], 1e-3, places=12)
        parsed = {"residuals": {"T": ser}}
        fc = cfd_report.residual_decay_forecast(parsed)
        self.assertIn("T", fc)
        self.assertAlmostEqual(fc["T"]["last"], 1e-3, places=12)
        self.assertAlmostEqual(fc["T"]["iters_to_target"], 458, delta=10)

    def test_stalled_residual_reports_unreachable(self):
        parsed = {"residuals": {"T": [1e-3] * 200}}
        fc = cfd_report.residual_decay_forecast(parsed)
        self.assertIsNone(fc["T"]["iters_to_target"])

    def test_already_converged_reports_zero(self):
        parsed = {"residuals": {"T": [1e-6] * 200}}
        fc = cfd_report.residual_decay_forecast(parsed)
        self.assertEqual(fc["T"]["iters_to_target"], 0)


class DiffuserResolution(unittest.TestCase):
    """급배기구가 격자에 뭉개지면 제트 관련 수치를 못 믿는다."""

    def test_flags_under_resolved_openings(self):
        dr = cfd_export.diffuser_resolution(ACCIDENT_CFG, ACCIDENT_PATCHES)
        self.assertIsNotNone(dr)
        # 0.045 m² → 한 변 0.212 m, 셀 0.15 m → 1.4셀 (< 2셀 기준)
        self.assertAlmostEqual(dr["worst"]["cells_per_side"], 1.41, delta=0.02)
        self.assertEqual(len(dr["under"]), 30)
        self.assertGreater(dr["recommended_cell_m"], 0)
        self.assertLess(dr["recommended_cell_m"], dr["cell_m"])

    def test_well_resolved_openings_not_flagged(self):
        cfg = dict(ACCIDENT_CFG, mesh={"cell": 0.05})
        dr = cfd_export.diffuser_resolution(cfg, ACCIDENT_PATCHES)
        self.assertEqual(dr["under"], [])

    def test_none_without_patches(self):
        self.assertIsNone(cfd_export.diffuser_resolution(ACCIDENT_CFG, None))


class OpeningParentPreflight(unittest.TestCase):
    def test_4way_quadrants_are_aggregated_as_one_terminal(self):
        cfg = {
            "room": {"L": 6.0, "W": 5.0, "H": 3.0},
            "inlet": {"T": 293.15},
            "openings": [
                {
                    "role": "supply", "type": "4way", "wall": "ceiling",
                    "cx": 1.0, "cy": 1.0, "w": 0.6, "h": 0.6, "cmh": 720.0,
                },
                {
                    "role": "exhaust", "type": "grille", "wall": "ceiling",
                    "cx": 4.0, "cy": 3.0, "w": 0.6, "h": 0.6, "cmh": 720.0,
                },
            ],
        }
        patches = cfd_export.resolve_openings(
            cfg, {"nx": 30, "ny": 25, "nz": 15}
        )
        preflight = cfd_export.opening_preflight(cfg, patches)
        supply = next(item for item in preflight["terminals"]
                      if item["role"] == "supply")

        self.assertEqual(preflight["contract"], "opening_preflight.v2")
        self.assertEqual(supply["child_patch_count"], 4)
        self.assertEqual(supply["parent_name"], "sup0")
        self.assertAlmostEqual(supply["design_cmh"], 720.0)
        self.assertAlmostEqual(supply["applied_normal_cmh"], 720.0, places=1)
        self.assertEqual(supply["flow_control"], "fixed_normal_velocity")
        self.assertAlmostEqual(supply["requested_area_m2"], 0.36)
        self.assertAlmostEqual(supply["snapped_area_m2"], 0.36)
        self.assertTrue(supply["area_within_tolerance"])

        exhaust = next(item for item in preflight["terminals"]
                       if item["role"] == "exhaust")
        self.assertAlmostEqual(exhaust["design_cmh"], 720.0)
        self.assertEqual(exhaust["flow_control"], "pressure_outlet")
        self.assertIsNone(exhaust["applied_normal_cmh"])
        self.assertIsNone(exhaust["solved_cmh"])
        self.assertEqual(exhaust["flow_status"], "RESULT_REQUIRED")

    def test_4way_directional_imbalance_blocks_jet_metrics_only(self):
        cfg = {
            "room": {"L": 6.0, "W": 5.0, "H": 3.0},
            "inlet": {"T": 293.15},
            "openings": [
                {
                    "role": "supply", "type": "4way", "wall": "ceiling",
                    "cx": 2.0, "cy": 2.0, "w": 0.6, "h": 0.6, "cmh": 720.0,
                    "opening_id": "DXF-DIFFUSER-17", "source_label": "SA-17",
                },
                {
                    "role": "exhaust", "type": "grille", "wall": "ceiling",
                    "cx": 4.0, "cy": 3.0, "w": 0.6, "h": 0.6, "cmh": 720.0,
                },
            ],
        }
        patches = cfd_export.resolve_openings(
            cfg, {"nx": 30, "ny": 25, "nz": 15}
        )
        supply = next(item for item in cfd_export.opening_preflight(cfg, patches)["terminals"]
                      if item["role"] == "supply")

        self.assertEqual(supply["opening_id"], "DXF-DIFFUSER-17")
        self.assertEqual(supply["source_label"], "SA-17")
        self.assertTrue(supply["area_within_tolerance"])
        self.assertFalse(supply["quadrant_balance_ok"])
        self.assertFalse(supply["quadrant_resolution_ok"])
        self.assertFalse(supply["jet_metrics_citable"])
        self.assertEqual(supply["status"], "WARN")


class HeatSourceMagnitude(unittest.TestCase):
    """fvOptions 에 주입되는 Su 가 요청 kW 와 정확히 대응해야 한다.

    Boussinesq 는 kinematic 이라 T 방정식 소스 단위가 K·m³/s 다.
    Su = P/(ρ0·cp) 이므로 역산 P = Su·ρcp 가 입력 kW 와 같아야 폐합 100%가 성립한다.
    (사고 케이스 검증: 12.8524 × 1206 = 15,500 W — 발열원 자체는 정상이었다.)
    """

    def test_su_round_trips_to_requested_kw(self):
        for kw in (5.0, 15.5, 46.0):
            cfg = dict(ACCIDENT_CFG, heat={"power_kw": kw})
            text = cfd_export.gen_fvoptions(cfg)
            su = float(text.split("T (")[1].split()[0])
            self.assertAlmostEqual(su * cfd_export.RHO_CP, kw * 1000.0, delta=1.0,
                                   msg=f"{kw} kW 주입량 불일치")

    def test_accident_case_su_value_is_reproduced(self):
        text = cfd_export.gen_fvoptions(ACCIDENT_CFG)
        self.assertIn("12.8524", text)      # 실제 사고 케이스 생성값


class SolverStopCriteria(unittest.TestCase):
    """residualControl 이 느슨하면 온도장이 초기값 근처에서 '수렴' 선언된다."""

    def test_template_residual_control_is_tight_enough(self):
        import os
        import re
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "cfd_templates", "elec_heat_bsq", "system", "fvSolution")
        text = open(path, encoding="utf-8").read()
        # 주석에도 'residualControl' 이 나오므로 실제 딕셔너리 블록만 정규식으로 집는다.
        match = re.search(r"^\s*residualControl\s*\{(.*?)^\s*\}", text,
                          re.S | re.M)
        self.assertIsNotNone(match, "residualControl 블록을 찾지 못함")
        block = match.group(1)
        for field in ("U", "T"):
            line = [ln for ln in block.splitlines()
                    if ln.strip().startswith(field + " ")]
            self.assertTrue(line, f"{field} residualControl 누락")
            value = float(line[0].split()[1].rstrip(";"))
            self.assertLessEqual(
                value, 1e-5,
                f"{field} 수렴기준이 느슨하면 미수렴 온도장이 '수렴'으로 보고된다")


if __name__ == "__main__":
    unittest.main()
