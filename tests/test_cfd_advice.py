"""설계 개선 추천 엔진 회귀 테스트.

핵심 계약: **미수렴 결과로는 설계 추천을 하지 않는다.**
틀린 결과 위에 추천을 얹으면 틀린 설계를 자동으로 배포하게 된다
(2026-08 사고: 에너지폐합 158%, 온도가 사실상 초기값).
"""
import unittest

import cfd_advice


ROOM = {"L": 27.6, "W": 15.9, "H": 10.0}


def _patch(name, role, cx, cy, area_req=0.0441, area_snap=0.045, cmh=444.0,
           wall="ceiling", U=(0.0, 0.0, -2.7407)):
    half = (area_req ** 0.5) / 2.0
    return {"name": name, "role": role, "wall": wall, "area": area_snap,
            "cmh": cmh, "cmh_req": cmh, "U": list(U),
            "rect_req": [cx - half, cy - half, cx + half, cy + half],
            "rect_snap": [cx - half, cy - half, cx + half, cy + half]}


def _meta(patches, equipment=33, via=None):
    return {"config": {"room": ROOM, "mesh": {"cell": 0.15}, "heat": {"power_kw": 15.5}},
            "patches": patches, "heat": {"via": via},
            "from_geometry": {"equipment": equipment}}


BASE_METRICS = {"heat_kw": 15.5, "T_supply_C": 16.0, "supply_cmh": 6660.0,
                "ach": 1.52, "room_volume": 4388.4, "closure_pct": 158.0}
GOOD_TRUST = {"citable": True, "badge": "수렴·폐합 양호(99%)", "reasons": []}
BAD_TRUST = {"citable": False, "badge": "결과 인용 불가(폐합 158%)",
             "reasons": ["에너지 폐합율 158% — 정상상태에서는 100%여야 한다."]}


def _case_health(citation_status, *, purpose="design_review_candidate",
                 field_status="PASS", reason="DESIGN_CITABLE"):
    field_reason = [] if field_status == "PASS" else ["FIELD_EVIDENCE_INVALID"]
    return {
        "contract": "case_health.v1",
        "purpose": purpose,
        "citation_status": citation_status,
        "errors": [{"code": reason}],
        "evidence": {
            "contract": "case_evidence.v1",
            "path": "_body_solver/case-a/case_evidence.v1.json",
            "sha256": "a" * 64,
        },
        "checks": {
            "design_ready": {
                "status": "PASS" if citation_status == "DESIGN_CITABLE" else "NOT_EVALUATED",
                "reason_codes": [] if citation_status == "DESIGN_CITABLE" else [reason],
                "evidence_refs": [],
                "impact": "현재 설계 인용 상태를 확인해야 합니다.",
                "next_actions": ["현재 증적과 검토 상태를 다시 확인하세요."],
            },
            "field_calibrated": {
                "status": field_status,
                "reason_codes": field_reason,
                "evidence_refs": [] if field_status == "PASS" else ["field_evidence"],
                "impact": "현장 보정 증적이 아직 평가되지 않았습니다.",
                "next_actions": ["현장 측정 및 TAB 증적을 등록하세요."],
            },
        },
    }


class TrustGate(unittest.TestCase):
    def test_unconverged_result_emits_blocking_recommendation_first(self):
        recs = cfd_advice.recommendations(
            _meta([_patch("sup0", "supply", 0.2, 12.0)]), BASE_METRICS,
            {"n_iters": 1000}, BAD_TRUST,
            {"Uz": {"iters_to_target": 27139, "last": 1e-3, "target": 1e-5,
                    "rate_per_iter": -1.7e-4}})
        self.assertEqual(recs[0]["priority"], cfd_advice.P_BLOCK)
        self.assertIn("재실행", recs[0]["action"])
        self.assertIn("27,139", recs[0]["action"])   # 남은 반복수를 숫자로 제시

    def test_converged_result_has_no_blocking_item(self):
        recs = cfd_advice.recommendations(
            _meta([_patch("sup0", "supply", 0.2, 12.0)]),
            dict(BASE_METRICS, closure_pct=99.0), {"n_iters": 5000}, GOOD_TRUST, {})
        self.assertFalse(any(r["priority"] == cfd_advice.P_BLOCK for r in recs))

    def test_authoritative_health_blocks_legacy_citable_true_and_is_first(self):
        recs = cfd_advice.recommendations(
            _meta([_patch("sup0", "supply", 0.2, 12.0)]),
            BASE_METRICS, None, GOOD_TRUST, {},
            case_health=_case_health(
                "CITATION_BLOCKED", reason="REVIEW_REJECTED"
            ),
        )
        self.assertEqual(recs[0]["priority"], cfd_advice.P_BLOCK)
        self.assertEqual(recs[0]["group"], "evidence")
        self.assertEqual(recs[0]["group_label"], "증적·검토")
        self.assertIn("REVIEW_REJECTED", recs[0]["basis"])

    def test_authoritative_design_health_overrides_stale_legacy_false(self):
        recs = cfd_advice.recommendations(
            _meta([_patch("sup0", "supply", 0.2, 12.0)]),
            dict(BASE_METRICS, closure_pct=99.0), None, BAD_TRUST, {},
            health=_case_health("DESIGN_CITABLE"),
        )
        self.assertFalse(any(row["priority"] == cfd_advice.P_BLOCK for row in recs))

    def test_invalid_health_shape_fails_closed(self):
        recs = cfd_advice.recommendations(
            _meta([_patch("sup0", "supply", 0.2, 12.0)]),
            BASE_METRICS, None, GOOD_TRUST, {}, case_health={"citation_status": "DESIGN_CITABLE"},
        )
        self.assertEqual(recs[0]["group"], "evidence")
        self.assertEqual(recs[0]["priority"], cfd_advice.P_BLOCK)


class RecommendationGroups(unittest.TestCase):
    def test_adds_group_fields_after_the_five_legacy_fields_without_rewording(self):
        patches = [
            _patch("sup0", "supply", 5.0, 5.0),
            _patch("exh0", "exhaust", 5.5, 5.0),
        ]
        recs = cfd_advice.recommendations(
            _meta(patches), BASE_METRICS, None, BAD_TRUST, {}
        )
        self.assertEqual(list(recs[0])[:5], [
            "priority", "category", "finding", "action", "basis",
        ])
        self.assertEqual(set(recs[0]) - {
            "priority", "category", "finding", "action", "basis",
        }, {"group", "group_label"})
        self.assertEqual(recs[0]["finding"],
                         "결과가 수렴/에너지수지 기준을 통과하지 못했습니다. "
                         "에너지 폐합율 158% — 정상상태에서는 100%여야 한다.")
        by_category = {row["category"]: row["group"] for row in recs}
        self.assertEqual(by_category["해석 신뢰도"], "evidence")
        self.assertEqual(by_category["풍량"], "input")
        self.assertEqual(by_category["급배기 배치"], "model")
        self.assertEqual(by_category["발열 모델"], "model")

    def test_required_missing_field_evidence_stays_not_evaluated_in_field_group(self):
        health = _case_health(
            "NOT_EVALUATED", purpose="field_validation",
            field_status="NOT_EVALUATED", reason="REQUIRED_CHECK_NOT_EVALUATED",
        )
        recs = cfd_advice.recommendations(
            _meta([_patch("sup0", "supply", 0.2, 12.0)]),
            BASE_METRICS, None, GOOD_TRUST, {}, case_health=health,
        )
        field = [row for row in recs if row["group"] == "field"]
        self.assertEqual(len(field), 1)
        self.assertIn("NOT_EVALUATED", field[0]["finding"])
        self.assertNotIn("PASS", field[0]["finding"])
        self.assertEqual(field[0]["group_label"], "현장 검증")


class RequiredAirflow(unittest.TestCase):
    def test_formula_matches_energy_balance(self):
        # 15.5 kW, 급기 16 °C, 목표 24 °C → ΔT 8 K → 15500/(1206·8)·3600
        cmh = cfd_advice.required_airflow_cmh(15500.0, 16.0, 24.0)
        self.assertAlmostEqual(cmh, 5784.0, delta=5.0)

    def test_returns_none_when_supply_hotter_than_target(self):
        self.assertIsNone(cfd_advice.required_airflow_cmh(15500.0, 30.0, 26.0))

    def test_flags_insufficient_airflow_against_target(self):
        meta = _meta([_patch("sup0", "supply", 0.2, 12.0)])
        meta["config"]["design"] = {"target_room_T_C": 20.0}   # 필요 11,568 CMH
        recs = cfd_advice.recommendations(meta, BASE_METRICS, None, GOOD_TRUST, {})
        flow = [r for r in recs if r["category"] == "풍량"]
        self.assertTrue(flow)
        self.assertEqual(flow[0]["priority"], cfd_advice.P_HIGH)
        self.assertIn("부족", flow[0]["finding"])

    def test_reports_table_when_target_unspecified(self):
        recs = cfd_advice.recommendations(
            _meta([_patch("sup0", "supply", 0.2, 12.0)]), BASE_METRICS,
            None, GOOD_TRUST, {})
        flow = [r for r in recs if r["category"] == "풍량"]
        self.assertTrue(flow)
        self.assertIn("보류", flow[0]["finding"])   # 추측하지 않는다


class SnapDistortion(unittest.TestCase):
    """격자 스냅으로 개구부 면적이 바뀌면 속도가 부풀려진다(설계 문제가 아님)."""

    def test_detects_halved_area_as_artifact(self):
        # 실제 사고: sup9 면적이 설계의 51% 로 잘려 속도가 2배
        patches = [_patch("sup0", "supply", 0.2, 12.0),
                   _patch("sup9", "supply", 9.7, 7.3, area_snap=0.0225)]
        recs = cfd_advice.recommendations(_meta(patches), BASE_METRICS,
                                          None, GOOD_TRUST, {})
        art = [r for r in recs if r["category"] == "개구부 모델링"]
        self.assertTrue(art)
        self.assertIn("sup9", art[0]["finding"])
        self.assertIn("2.0배", art[0]["finding"])
        self.assertIn("인공물", art[0]["finding"])

    def test_no_flag_when_snap_is_close_to_design(self):
        recs = cfd_advice.recommendations(
            _meta([_patch("sup0", "supply", 0.2, 12.0)]), BASE_METRICS,
            None, GOOD_TRUST, {})
        self.assertFalse([r for r in recs if r["category"] == "개구부 모델링"])

    def test_4way_children_use_parent_preflight_not_quadrant_area(self):
        patches = [
            _patch(f"sup0_q{index}", "supply", 4.0, 4.0,
                   area_req=0.16, area_snap=0.04, cmh=100.0)
            for index in range(4)
        ]
        meta = _meta(patches)
        meta["opening_preflight"] = {
            "contract": "opening_preflight.v2",
            "terminals": [{
                "parent_name": "sup0",
                "role": "supply",
                "requested_area_m2": 0.16,
                "snapped_area_m2": 0.16,
                "design_cmh": 400.0,
                "applied_normal_cmh": 400.0,
            }],
        }
        recs = cfd_advice.recommendations(
            meta, BASE_METRICS, None, GOOD_TRUST, {}
        )
        self.assertFalse([r for r in recs if r["category"] == "개구부 모델링"])

    def test_4way_face_velocity_uses_parent_terminal_flow_and_area(self):
        patches = [
            _patch(f"sup0_q{index}", "supply", 4.0, 4.0,
                   area_req=0.16, area_snap=0.04, cmh=750.0)
            for index in range(4)
        ]
        meta = _meta(patches)
        meta["opening_preflight"] = {
            "contract": "opening_preflight.v2",
            "terminals": [{
                "parent_name": "sup0",
                "role": "supply",
                "requested_area_m2": 0.16,
                "snapped_area_m2": 0.16,
                "design_cmh": 3000.0,
                "applied_normal_cmh": 3000.0,
            }],
        }
        recs = cfd_advice.recommendations(
            meta, BASE_METRICS, None, GOOD_TRUST, {}
        )
        face = [r for r in recs if r["category"] == "디퓨저 사양"]
        self.assertTrue(face)
        self.assertIn("sup0", face[0]["finding"])


class ShortCircuit(unittest.TestCase):
    def test_flags_adjacent_supply_and_exhaust(self):
        patches = [_patch("sup0", "supply", 5.0, 5.0),
                   _patch("exh0", "exhaust", 5.5, 5.0)]      # 0.5 m 간격
        recs = cfd_advice.recommendations(_meta(patches), BASE_METRICS,
                                          None, GOOD_TRUST, {})
        sc = [r for r in recs if r["category"] == "급배기 배치"]
        self.assertEqual(sc[0]["priority"], cfd_advice.P_HIGH)
        self.assertIn("단락류", sc[0]["finding"])

    def test_well_separated_layout_is_informational(self):
        patches = [_patch("sup0", "supply", 0.2, 12.0),
                   _patch("exh0", "exhaust", 4.9, 12.0)]     # 4.7 m
        recs = cfd_advice.recommendations(_meta(patches), BASE_METRICS,
                                          None, GOOD_TRUST, {})
        sc = [r for r in recs if r["category"] == "급배기 배치"]
        self.assertEqual(sc[0]["priority"], cfd_advice.P_INFO)


class HotspotModelling(unittest.TestCase):
    def test_warns_when_equipment_detected_but_homogenised(self):
        recs = cfd_advice.recommendations(
            _meta([_patch("sup0", "supply", 0.2, 12.0)], equipment=33, via=None),
            BASE_METRICS, None, GOOD_TRUST, {})
        hs = [r for r in recs if r["category"] == "발열 모델"]
        self.assertTrue(hs)
        self.assertIn("33대", hs[0]["finding"])

    def test_silent_when_equipment_zones_modelled(self):
        recs = cfd_advice.recommendations(
            _meta([_patch("sup0", "supply", 0.2, 12.0)], via="obstacles"),
            BASE_METRICS, None, GOOD_TRUST, {})
        self.assertFalse([r for r in recs if r["category"] == "발열 모델"])


class Digest(unittest.TestCase):
    """LLM 입력용 다이제스트 — 사실만, 그리고 인용 금지 표식이 살아있어야 한다."""

    def _digest(self, trust):
        meta = _meta([_patch("sup0", "supply", 0.2, 12.0),
                      _patch("exh0", "exhaust", 4.9, 12.0)])
        metrics = dict(BASE_METRICS, T_avg_C=26.66, T_max_C=27.22,
                       dT_rise=10.66, U_max=5.38, T_eq_C=22.95)
        recs = cfd_advice.recommendations(meta, metrics, {"n_iters": 1000}, trust, {})
        return cfd_advice.ai_digest(meta, metrics, {"n_iters": 1000}, trust, {}, recs)

    def test_unconverged_digest_tells_ai_not_to_conclude(self):
        d = self._digest(BAD_TRUST)
        self.assertIn("설계 결론을 내리지 마십시오", d)
        self.assertIn("인용 금지", d)
        self.assertIn("22.9", d)          # 이론 평형온도 교차검증값 포함

    def test_converged_digest_has_no_block_warning(self):
        d = self._digest(GOOD_TRUST)
        self.assertNotIn("설계 결론을 내리지 마십시오", d)

    def test_digest_is_plain_markdown_without_html_tags(self):
        """HTML 태그가 새면 LLM 입력이 지저분해진다 — 원본은 마크다운이어야 한다."""
        patches = [_patch("sup0", "supply", 0.2, 12.0),
                   _patch("sup9", "supply", 9.7, 7.3, area_snap=0.0225)]
        meta = _meta(patches)
        recs = cfd_advice.recommendations(meta, BASE_METRICS, None, GOOD_TRUST, {})
        d = cfd_advice.ai_digest(meta, BASE_METRICS, None, GOOD_TRUST, {}, recs)
        self.assertNotIn("<b>", d)
        self.assertNotIn("</b>", d)

    def test_digest_is_small_enough_to_paste(self):
        d = self._digest(BAD_TRUST)
        self.assertLess(len(d), 20000, "다이제스트는 붙여넣기 가능한 크기여야 한다")

    def test_json_payload_is_valid(self):
        import json
        meta = _meta([_patch("sup0", "supply", 0.2, 12.0)])
        recs = cfd_advice.recommendations(meta, BASE_METRICS, None, BAD_TRUST, {})
        payload = json.loads(cfd_advice.digest_payload(
            meta, BASE_METRICS, {"n_iters": 1000}, BAD_TRUST, {}, recs))
        self.assertFalse(payload["citable"])
        self.assertEqual(payload["n_iters"], 1000)
        self.assertTrue(payload["recommendations"])

    def test_authoritative_health_seals_markdown_and_json_despite_legacy_trust(self):
        import json
        meta = _meta([_patch("sup0", "supply", 0.2, 12.0)])
        health = _case_health(
            "CITATION_BLOCKED", reason="ARTIFACT_HASH_MISMATCH"
        )
        recs = cfd_advice.recommendations(
            meta, BASE_METRICS, None, GOOD_TRUST, {}, case_health=health,
        )
        digest = cfd_advice.ai_digest(
            meta, BASE_METRICS, None, GOOD_TRUST, {}, recs,
            case_health=health,
        )
        payload = json.loads(cfd_advice.digest_payload(
            meta, BASE_METRICS, None, GOOD_TRUST, {}, recs,
            case_health=health,
        ))
        self.assertIn("설계 결론을 내리지 마십시오", digest)
        self.assertIn("CITATION_BLOCKED", digest)
        self.assertFalse(payload["citable"])
        self.assertEqual(payload["citation_status"], "CITATION_BLOCKED")


if __name__ == "__main__":
    unittest.main()
