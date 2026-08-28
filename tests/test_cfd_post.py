import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import cfd_post


def _vtu(cells, volumes=None, *, points=None, temperatures=None, velocities=None,
         time_s=0.5):
    points = [
        (0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
        (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1),
        (2, 0, 0), (3, 0, 0), (3, 1, 0), (2, 1, 0),
        (2, 0, 1), (3, 0, 1), (3, 1, 1), (2, 1, 1),
    ] if points is None else points
    definitions = {
        "left": {"ids": list(range(8)), "T": 294.0, "U": (1.0, 0.0, 0.0)},
        "right": {"ids": list(range(8, 16)), "T": 300.0, "U": (0.0, 2.0, 0.0)},
    }
    connectivity, offsets, temperature, velocity = [], [], [], []
    for index, name in enumerate(cells):
        item = definitions[name]
        connectivity.extend(item["ids"])
        offsets.append(len(connectivity))
        temperature.append(item["T"] if temperatures is None else temperatures[index])
        velocity.extend(item["U"] if velocities is None else velocities[index])
    point_text = " ".join(str(value) for point in points for value in point)
    volume_text = ""
    if volumes is not None:
        volume_text = (
            "\n    <DataArray type='Float32' Name='V' format='ascii'>"
            + " ".join(map(str, volumes))
            + "</DataArray>"
        )
    return f"""<?xml version='1.0'?>
<VTKFile type='UnstructuredGrid' version='0.1' byte_order='LittleEndian'>
 <UnstructuredGrid>
  <FieldData><DataArray type='Float32' Name='TimeValue' format='ascii'>{time_s}</DataArray></FieldData>
  <Piece NumberOfPoints='16' NumberOfCells='{len(cells)}'>
   <Points><DataArray type='Float32' Name='Points' NumberOfComponents='3' format='ascii'>{point_text}</DataArray></Points>
   <Cells>
    <DataArray type='Int32' Name='connectivity' format='ascii'>{' '.join(map(str, connectivity))}</DataArray>
    <DataArray type='Int32' Name='offsets' format='ascii'>{' '.join(map(str, offsets))}</DataArray>
   </Cells>
   <CellData>
    <DataArray type='Float32' Name='T' format='ascii'>{' '.join(map(str, temperature))}</DataArray>
    <DataArray type='Float32' Name='U' NumberOfComponents='3' format='ascii'>{' '.join(map(str, velocity))}</DataArray>
    {volume_text}
   </CellData>
  </Piece>
 </UnstructuredGrid>
</VTKFile>
"""


class BodyFittedPostTests(unittest.TestCase):
    def test_vtu_summary_is_independent_of_cell_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.vtu"
            second = root / "second.vtu"
            first.write_text(_vtu(["left", "right"]), encoding="ascii")
            second.write_text(_vtu(["right", "left"]), encoding="ascii")
            a = cfd_post.summarize_vtu(first)
            b = cfd_post.summarize_vtu(second)
        self.assertEqual(a["temperature"]["minimum"], 294.0)
        self.assertEqual(a["temperature"]["maximum"], 300.0)
        self.assertEqual(a["temperature"]["mean"], b["temperature"]["mean"])
        self.assertEqual(a["velocity"]["maximum_speed"], 2.0)
        self.assertEqual(
            a["temperature"]["hottest_cell"]["centre_m"],
            b["temperature"]["hottest_cell"]["centre_m"],
        )
        self.assertEqual([row["sample_count"] for row in a["slices"]], [2, 2, 2])

    def test_result_artifacts_record_hashes_fields_and_slices(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp)
            source = case / "VTK" / "case_1" / "internal.vtu"
            source.parent.mkdir(parents=True)
            source.write_text(_vtu(["left", "right"], [1.0, 3.0]), encoding="ascii")
            (case / "mesh_manifest.json").write_text('{"status":"PASS"}', encoding="utf-8")
            (case / "run_manifest.json").write_text('{"status":"WARN"}', encoding="utf-8")
            (case / "thermal_input.json").write_text(json.dumps({
                "contract": "thermal_input.v1",
                "settings": {
                    "occupied_volume_selector": {
                        "contract": "occupied_volume_band.v1",
                        "coordinate_source": "cell_center_m_agl",
                        "z_min_agl_m": 0.0,
                        "z_max_agl_m": 1.0,
                    },
                    "occupied_floor_elevation_m": 0.0,
                },
            }), encoding="utf-8")
            result = cfd_post.build_result_artifacts(case)
            manifest = json.loads((case / "result_manifest.json").read_text(encoding="utf-8"))
            summary = json.loads((case / manifest["summary_path"]).read_text(encoding="utf-8"))
            summary_hash = cfd_post._sha256(case / manifest["summary_path"])
            thermal_hash = cfd_post._sha256(case / "thermal_input.json")
            slice_hashes = {
                item["path"]: cfd_post._sha256(case / item["path"])
                for item in manifest["slices"]
            }
            qoi = json.loads((case / manifest["occupied_qoi"]["path"]).read_text(
                encoding="utf-8"
            ))
            qoi_hash = cfd_post._sha256(case / manifest["occupied_qoi"]["path"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(manifest["contract"], "result_manifest.v1")
        self.assertEqual(manifest["field_location"], "cell")
        self.assertEqual(set(manifest["fields"]), {"T", "U", "V"})
        self.assertEqual(len(manifest["slices"]), 3)
        self.assertEqual(
            manifest["summary_sha256"],
            summary_hash,
        )
        self.assertEqual(
            manifest["thermal_input_sha256"],
            thermal_hash,
        )
        for item in manifest["slices"]:
            self.assertEqual(item["sha256"], slice_hashes[item["path"]])
        self.assertEqual(summary["aggregation"], "cell_count_unweighted")
        self.assertEqual(manifest["occupied_qoi"]["sha256"], qoi_hash)
        self.assertEqual(qoi["scope"], "selected_occupied_volume_band")
        self.assertAlmostEqual(qoi["temperature"]["mean_k"], 298.5)
        self.assertAlmostEqual(qoi["temperature"]["p95_k"], 300.0)
        self.assertAlmostEqual(qoi["velocity"]["p95_speed_m_s"], 2.0)


class NumericalSensitivityPostprocessTests(unittest.TestCase):
    @staticmethod
    def _selector():
        return {
            "contract": "occupied_volume_band.v1",
            "coordinate_source": "cell_center_m_agl",
            "z_min_agl_m": 0.0,
            "z_max_agl_m": 1.0,
        }

    def test_reads_optional_cell_volumes_without_changing_unweighted_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "internal.vtu"
            source.write_text(_vtu(["left", "right"], [1.0, 3.0]), encoding="ascii")
            parsed = cfd_post.read_internal_vtu(source)
            summary = cfd_post.summarize_vtu(source)

        self.assertEqual(parsed["volume_m3"], [1.0, 3.0])
        self.assertEqual(summary["aggregation"], "cell_count_unweighted")
        self.assertEqual(summary["fields"]["V"]["association"], "cell")

    def test_computes_occupied_qois_from_vtu_with_volume_weights_and_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "internal.vtu"
            source.write_text(_vtu(["left", "right"], [1.0, 3.0]), encoding="ascii")

            qois = cfd_post.compute_occupied_volume_qois_from_vtu(
                source, self._selector(), floor_elevation_m=0.0
            )

        self.assertEqual(qois["contract"], "occupied_volume_qoi.v1")
        self.assertEqual(qois["selected_cell_count"], 2)
        self.assertAlmostEqual(qois["selected_volume_m3"], 4.0)
        self.assertAlmostEqual(qois["occupied_zone_mean_temperature_k"], 298.5)
        self.assertAlmostEqual(qois["occupied_zone_mean_speed_m_s"], 1.75)
        self.assertAlmostEqual(qois["temperature"]["p95_k"], 300.0)
        self.assertAlmostEqual(qois["velocity"]["p95_speed_m_s"], 2.0)
        self.assertRegex(qois["source_vtu_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(qois["selector_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(qois["selector"]["coordinate_source"], "cell_center_m_agl")
        self.assertEqual(
            qois["coordinate_provenance"],
            {
                "source_coordinate": "vtu_mesh_coordinates_m",
                "floor_elevation_m": 0.0,
                "output_coordinate": "cell_center_m_agl",
            },
        )

    def test_time_weights_volume_weighted_occupied_qois_over_final_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = []
            for index, time_s in enumerate((9.0, 9.25, 9.5, 9.75, 10.0)):
                temperature = 294.0 + index
                speed = 1.0 + 0.25 * index
                source = root / f"internal-{index}.vtu"
                source.write_text(_vtu(
                    ["left", "right"], [1.0, 3.0],
                    temperatures=[temperature, temperature],
                    velocities=[(speed, 0.0, 0.0), (speed, 0.0, 0.0)],
                    time_s=time_s,
                ), encoding="ascii")
                sources.append(source)

            qois = cfd_post.compute_time_weighted_occupied_volume_qois(
                sources,
                self._selector(),
                floor_elevation_m=0.0,
                window_start_s=9.0,
                window_end_s=10.0,
                minimum_samples=5,
            )

        self.assertEqual(
            qois["aggregation"],
            "time_weighted_trapezoidal_of_cell_volume_weighted_snapshots.v1",
        )
        self.assertEqual(qois["sample_count"], 5)
        self.assertEqual(qois["window"], {"start_s": 9.0, "end_s": 10.0})
        self.assertAlmostEqual(qois["occupied_zone_mean_temperature_k"], 296.0)
        self.assertAlmostEqual(qois["occupied_zone_mean_speed_m_s"], 1.5)
        self.assertEqual(len(qois["sources"]), 5)
        self.assertTrue(all(len(item["sha256"]) == 64 for item in qois["sources"]))

    def test_time_weighted_occupied_qoi_fails_closed_on_sparse_or_uncovered_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = []
            for index, time_s in enumerate((9.25, 9.5, 9.75, 10.0)):
                source = root / f"internal-{index}.vtu"
                source.write_text(
                    _vtu(["left", "right"], [1.0, 3.0], time_s=time_s),
                    encoding="ascii",
                )
                sources.append(source)
            with self.assertRaisesRegex(
                    cfd_post.PostprocessEvidenceError,
                    "TIME_WEIGHTED_QOI_WINDOW_NOT_COVERED"):
                cfd_post.compute_time_weighted_occupied_volume_qois(
                    sources, self._selector(), floor_elevation_m=0.0,
                    window_start_s=9.0, window_end_s=10.0, minimum_samples=5,
                )

    def test_time_weights_solver_derived_exhaust_rise_samples(self):
        samples = []
        for index, time_s in enumerate((9.0, 9.25, 9.5, 9.75, 10.0)):
            samples.append({
                "time_s": time_s,
                "source_refs": [
                    {"path": f"{time_s}/T", "sha256": "a" * 64},
                    {"path": f"{time_s}/phi", "sha256": "b" * 64},
                ],
                "exhausts": [{
                    "mesh_patch_name": "exhaust",
                    "temperature_k": 298.15 + index,
                    "solved_outflow_rate_m3_s": 1.0,
                    "temperature_method": (
                        "positive_phi_weighted_owner_cell_temperature"
                    ),
                }],
            })

        qoi = cfd_post.compute_time_weighted_exhaust_temperature_rise_qoi(
            samples,
            supply_temperature_k=293.15,
            window_start_s=9.0,
            window_end_s=10.0,
            minimum_samples=5,
        )

        self.assertEqual(qoi["sample_count"], 5)
        self.assertAlmostEqual(qoi["exhaust_temperature_rise_k"], 7.0)
        self.assertAlmostEqual(qoi["flow_weighted_exhaust_temperature_k"], 300.15)
        self.assertEqual(
            qoi["provenance"]["temperature_method"],
            "positive_phi_weighted_owner_cell_temperature",
        )

    def test_time_weighted_exhaust_rejects_fallback_or_uncovered_samples(self):
        samples = [{
            "time_s": time_s,
            "source_refs": [],
            "exhausts": [{
                "mesh_patch_name": "exhaust",
                "temperature_k": 300.0,
                "solved_outflow_rate_m3_s": 1.0,
                "temperature_method": "fallback",
            }],
        } for time_s in (9.0, 9.25, 9.5, 9.75, 10.0)]
        with self.assertRaisesRegex(
                cfd_post.PostprocessEvidenceError,
                "EXHAUST_TIME_QOI_SOLVER_PROVENANCE_REQUIRED"):
            cfd_post.compute_time_weighted_exhaust_temperature_rise_qoi(
                samples, supply_temperature_k=293.15,
                window_start_s=9.0, window_end_s=10.0, minimum_samples=5,
            )

    def test_reads_time_weighted_exhaust_from_raw_saved_T_and_phi_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp)
            (case / "thermal_input.json").write_text(json.dumps({
                "settings": {"supply_temperature_k": 293.15},
                "terminals": [
                    {"role": "supply", "mesh_patch_name": "supply"},
                    {"role": "exhaust", "mesh_patch_name": "exhaust"},
                ],
            }), encoding="utf-8")
            for index, time_s in enumerate((9.0, 9.25, 9.5, 9.75, 10.0)):
                time_dir = case / str(time_s)
                time_dir.mkdir()
                (time_dir / "T").write_text(
                    "internalField uniform 300;", encoding="utf-8")
                (time_dir / "phi").write_text(
                    "boundaryField { exhaust { value uniform 1; } }", encoding="utf-8")

            with mock.patch("cfd_physics._exhaust_flux_temperature") as extract:
                extract.side_effect = [
                    {"temperature_k": 298.15 + index, "flow_rate_m3_s": 1.0,
                     "method": "positive_phi_weighted_owner_cell_temperature"}
                    for index in range(5)
                ]
                qoi = cfd_post.read_time_weighted_exhaust_temperature_rise_from_case(
                    case, window_start_s=9.0, window_end_s=10.0,
                    minimum_samples=5,
                )

        self.assertAlmostEqual(qoi["exhaust_temperature_rise_k"], 7.0)
        self.assertEqual(qoi["sample_count"], 5)
        self.assertEqual(len(qoi["samples"][0]["source_refs"]), 2)
        self.assertTrue(all(extract_call.args[1].name in {
            "9.0", "9.25", "9.5", "9.75", "10.0"
        } for extract_call in extract.call_args_list))

    def test_occupied_qoi_rejects_missing_mismatched_or_nonpositive_volume_data(self):
        cases = (
            ("missing", None, "OCCUPIED_VTU_VOLUME_MISSING"),
            ("mismatched", [1.0], "OCCUPIED_VTU_VOLUME_TUPLE_MISMATCH"),
            ("negative", [1.0, -2.0], "OCCUPIED_VTU_VOLUME_INVALID"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, volumes, code in cases:
                with self.subTest(name=name):
                    source = root / f"{name}.vtu"
                    source.write_text(_vtu(["left", "right"], volumes), encoding="ascii")
                    with self.assertRaisesRegex(cfd_post.PostprocessEvidenceError, code):
                        cfd_post.compute_occupied_volume_qois_from_vtu(
                            source, self._selector(), floor_elevation_m=0.0
                        )

    def test_occupied_qoi_requires_an_explicit_agl_floor_elevation(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "internal.vtu"
            source.write_text(_vtu(["left", "right"], [1.0, 3.0]), encoding="ascii")
            with self.assertRaisesRegex(
                    cfd_post.PostprocessEvidenceError,
                    "OCCUPIED_VTU_AGL_FLOOR_ELEVATION_REQUIRED"):
                cfd_post.compute_occupied_volume_qois_from_vtu(source, self._selector())

    def test_occupied_qoi_rejects_nonfinite_floor_geometry_and_field_values(self):
        points = [
            (0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
            (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1),
            (2, 0, 0), (3, 0, 0), (3, 1, 0), (2, 1, 0),
            (2, 0, 1), (3, 0, 1), (3, 1, 1), (2, 1, 1),
        ]
        invalid_points = list(points)
        invalid_points[0] = (math.nan, 0, 0)
        cases = (
            ("floor", _vtu(["left", "right"], [1.0, 3.0]), math.inf,
             "OCCUPIED_VTU_AGL_FLOOR_ELEVATION_INVALID"),
            ("volume", _vtu(["left", "right"], [math.nan, 3.0]), 0.0,
             "OCCUPIED_VTU_VOLUME_INVALID"),
            ("centre", _vtu(["left", "right"], [1.0, 3.0], points=invalid_points), 0.0,
             "OCCUPIED_VTU_CENTRE_INVALID"),
            ("temperature", _vtu(["left", "right"], [1.0, 3.0],
                                   temperatures=[math.nan, 300.0]), 0.0,
             "OCCUPIED_VTU_TEMPERATURE_INVALID"),
            ("velocity", _vtu(["left", "right"], [1.0, 3.0],
                                velocities=[(math.inf, 0.0, 0.0), (0.0, 2.0, 0.0)]), 0.0,
             "OCCUPIED_VTU_VELOCITY_INVALID"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, payload, floor_elevation_m, code in cases:
                with self.subTest(name=name):
                    source = root / f"{name}.vtu"
                    source.write_text(payload, encoding="ascii")
                    with self.assertRaisesRegex(cfd_post.PostprocessEvidenceError, code):
                        cfd_post.compute_occupied_volume_qois_from_vtu(
                            source, self._selector(), floor_elevation_m=floor_elevation_m
                        )

    def test_occupied_qoi_normalizes_and_applies_the_full_selector_contract(self):
        selector = self._selector()
        selector["xy_bounds_m"] = {
            "x_min_m": 0.0,
            "x_max_m": 1.0,
            "y_min_m": 0.0,
            "y_max_m": 1.0,
        }
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "internal.vtu"
            source.write_text(_vtu(["left", "right"], [1.0, 3.0]), encoding="ascii")
            qois = cfd_post.compute_occupied_volume_qois_from_vtu(
                source, selector, floor_elevation_m=0.0
            )

        self.assertEqual(qois["selected_cell_count"], 1)
        self.assertEqual(qois["selected_volume_m3"], 1.0)
        self.assertEqual(qois["occupied_zone_mean_temperature_k"], 294.0)
        self.assertEqual(
            qois["selector_sha256"],
            qois["selector"]["selector_sha256"],
        )

    def test_occupied_qoi_rejects_invalid_selector_as_postprocess_evidence_error(self):
        invalid_selectors = (
            None,
            {**self._selector(), "z_min_agl_m": math.nan},
            {**self._selector(), "unsupported": "field"},
        )
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "internal.vtu"
            source.write_text(_vtu(["left", "right"], [1.0, 3.0]), encoding="ascii")
            for selector in invalid_selectors:
                with self.subTest(selector=selector):
                    with self.assertRaisesRegex(
                            cfd_post.PostprocessEvidenceError,
                            "OCCUPIED_VTU_SELECTOR_INVALID"):
                        cfd_post.compute_occupied_volume_qois_from_vtu(
                            source, selector, floor_elevation_m=0.0
                        )

    def test_reads_trusted_solver_exhaust_temperature_rise_with_hash_provenance(self):
        run = {
            "contract": "run_manifest.v1",
            "engine": "body_fitted_buoyant_urans",
            "status": "WARN",
            "effective_settings": {"supply_temperature_k": 293.15},
            "thermal": {
                "available": True,
                "energy_closure_basis": "solver_positive_phi_and_owner_cell_temperature",
                "exhausts": [
                    {
                        "mesh_patch_name": "exhaust_left",
                        "temperature_k": 298.15,
                        "solved_outflow_rate_m3_s": 1.0,
                        "temperature_method": "positive_phi_weighted_owner_cell_temperature",
                    },
                    {
                        "mesh_patch_name": "exhaust_right",
                        "temperature_k": 303.15,
                        "solved_outflow_rate_m3_s": 3.0,
                        "temperature_method": "positive_phi_weighted_owner_cell_temperature",
                    },
                ],
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "run_manifest.json"
            source.write_text(json.dumps(run), encoding="utf-8")
            expected_hash = cfd_post._sha256(source)
            qoi = cfd_post.read_trusted_exhaust_temperature_rise_qoi(
                source, expected_run_manifest_sha256=expected_hash
            )

        self.assertEqual(qoi["contract"], "exhaust_temperature_rise_qoi.v1")
        self.assertAlmostEqual(qoi["exhaust_temperature_rise_k"], 8.75)
        self.assertAlmostEqual(qoi["flow_weighted_exhaust_temperature_k"], 301.9)
        self.assertEqual(qoi["run_manifest_sha256"], expected_hash)
        self.assertEqual(
            qoi["provenance"]["temperature_method"],
            "positive_phi_weighted_owner_cell_temperature",
        )

    def test_exhaust_temperature_rise_rejects_untrusted_or_non_solver_output(self):
        run = {
            "contract": "run_manifest.v1",
            "engine": "body_fitted_buoyant_urans",
            "status": "WARN",
            "effective_settings": {"supply_temperature_k": 293.15},
            "thermal": {
                "available": True,
                "energy_closure_basis": "design_flow_and_saved_boundary_temperature_fallback",
                "exhausts": [{
                    "mesh_patch_name": "exhaust",
                    "temperature_k": 300.15,
                    "solved_outflow_rate_m3_s": None,
                    "temperature_method": "design_flow_and_saved_boundary_temperature_fallback",
                }],
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "run_manifest.json"
            source.write_text(json.dumps(run), encoding="utf-8")
            with self.assertRaisesRegex(
                    cfd_post.PostprocessEvidenceError,
                    "EXHAUST_QOI_TRUSTED_MANIFEST_HASH_REQUIRED"):
                cfd_post.read_trusted_exhaust_temperature_rise_qoi(source)
            with self.assertRaisesRegex(
                    cfd_post.PostprocessEvidenceError,
                    "EXHAUST_QOI_SOLVER_PROVENANCE_REQUIRED"):
                cfd_post.read_trusted_exhaust_temperature_rise_qoi(
                    source, expected_run_manifest_sha256=cfd_post._sha256(source)
                )

    def test_exhaust_temperature_rise_rejects_nonfinite_or_malformed_thermal_values(self):
        def trusted_run():
            return {
                "contract": "run_manifest.v1",
                "effective_settings": {"supply_temperature_k": 293.15},
                "thermal": {
                    "energy_closure_basis": (
                        "solver_positive_phi_and_owner_cell_temperature"
                    ),
                    "exhausts": [{
                        "mesh_patch_name": "exhaust",
                        "temperature_k": 300.15,
                        "solved_outflow_rate_m3_s": 1.0,
                        "temperature_method": (
                            "positive_phi_weighted_owner_cell_temperature"
                        ),
                    }],
                },
            }

        cases = (
            ("nan_rate", lambda run: run["thermal"]["exhausts"][0].update(
                solved_outflow_rate_m3_s=math.nan)),
            ("infinite_temperature", lambda run: run["thermal"]["exhausts"][0].update(
                temperature_k=math.inf)),
            ("nan_supply", lambda run: run["effective_settings"].update(
                supply_temperature_k=math.nan)),
            ("text_rate", lambda run: run["thermal"]["exhausts"][0].update(
                solved_outflow_rate_m3_s="1.0")),
            ("malformed_exhausts", lambda run: run["thermal"].update(exhausts="not-a-list")),
            ("malformed_settings", lambda run: run.update(effective_settings=[])),
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, mutate in cases:
                with self.subTest(name=name):
                    run = trusted_run()
                    mutate(run)
                    source = root / f"{name}.json"
                    source.write_text(json.dumps(run), encoding="utf-8")
                    with self.assertRaisesRegex(
                            cfd_post.PostprocessEvidenceError,
                            "EXHAUST_QOI_VALUE_INVALID"):
                        cfd_post.read_trusted_exhaust_temperature_rise_qoi(
                            source,
                            expected_run_manifest_sha256=cfd_post._sha256(source),
                        )


if __name__ == "__main__":
    unittest.main()
