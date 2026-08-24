import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import cfd_export
import cfd_report
import cfd_run


BASE_CFG = {
    "name": "test",
    "room": {"L": 4.0, "W": 3.0, "H": 2.5},
    "mesh": {"cell": 0.5},
    "inlet": {"wall": "x0", "U": [0.3, 0, 0], "T": 293.15},
    "outlet": {"wall": "xL"},
    "heat": {"power_kw": 5.0},
    "init": {"T": 300.0},
    "endTime": 400,
}


class ConfigSafetyTests(unittest.TestCase):
    def test_zero_cell_is_rejected_with_field_message(self):
        cfg = copy.deepcopy(BASE_CFG)
        cfg["mesh"]["cell"] = 0
        with self.assertRaisesRegex(SystemExit, "격자 셀 크기"):
            cfd_export.validate_config(cfg)

    def test_excessive_cell_count_is_rejected(self):
        cfg = copy.deepcopy(BASE_CFG)
        cfg["mesh"]["cell"] = 0.01
        with self.assertRaisesRegex(SystemExit, "안전 한도"):
            cfd_export.validate_config(cfg, max_cells=1_000)

    def test_opening_outside_wall_is_not_clamped(self):
        cfg = copy.deepcopy(BASE_CFG)
        cfg["openings"] = [
            {"role": "supply", "type": "grille", "wall": "ceiling",
             "cx": 99.0, "cy": 1.0, "w": 0.5, "h": 0.5, "cmh": 400},
            {"role": "exhaust", "type": "grille", "wall": "xL",
             "cx": 1.0, "cy": 1.0, "w": 0.5, "h": 0.5},
        ]
        _, mesh = cfd_export.gen_blockmesh(cfg, {k: "wall" for k in cfd_export._FACES})
        with self.assertRaisesRegex(SystemExit, "벽 크기.*밖"):
            cfd_export.resolve_openings(cfg, mesh)

    def test_near_edge_opening_error_gives_exact_center_or_size_fix(self):
        cfg = copy.deepcopy(BASE_CFG)
        cfg["room"] = {"L": 27.6, "W": 15.9, "H": 10.0}
        cfg["mesh"]["cell"] = 0.2
        cfg["openings"] = [{
            "role": "supply", "type": "round", "wall": "ceiling",
            "cx": 0.1, "cy": 12.0, "w": 0.21, "h": 0.21, "cmh": 444,
        }]
        _, mesh = cfd_export.gen_blockmesh(
            cfg, {k: "wall" for k in cfd_export._FACES},
        )
        with self.assertRaises(SystemExit) as raised:
            cfd_export.resolve_openings(cfg, mesh)
        message = str(raised.exception)
        self.assertIn("1번", message)
        self.assertIn("cx를 0.105", message)
        self.assertIn("w를 0.200", message)

    def test_four_way_needs_at_least_two_by_two_cells(self):
        cfg = copy.deepcopy(BASE_CFG)
        cfg["mesh"]["cell"] = 1.0
        cfg["openings"] = [
            {"role": "supply", "type": "4way", "wall": "ceiling",
             "cx": 1.5, "cy": 1.5, "w": 0.4, "h": 0.4, "cmh": 400},
            {"role": "exhaust", "type": "grille", "wall": "xL",
             "cx": 1.5, "cy": 1.0, "w": 0.5, "h": 0.5},
        ]
        _, mesh = cfd_export.gen_blockmesh(cfg, {k: "wall" for k in cfd_export._FACES})
        with self.assertRaisesRegex(SystemExit, "4방향"):
            cfd_export.resolve_openings(cfg, mesh)

    def test_round_terminal_uses_single_normal_patch_on_coarse_mesh(self):
        cfg = copy.deepcopy(BASE_CFG)
        cfg["mesh"]["cell"] = 0.15
        cfg["openings"] = [
            {"role": "supply", "type": "round", "wall": "ceiling",
             "cx": 1.0, "cy": 1.0, "w": 0.2, "h": 0.2, "cmh": 400},
            {"role": "exhaust", "type": "grille", "wall": "ceiling",
             "cx": 3.0, "cy": 2.0, "w": 0.2, "h": 0.2, "cmh": 400},
        ]
        _, mesh = cfd_export.gen_blockmesh(cfg, {k: "wall" for k in cfd_export._FACES})
        patches = cfd_export.resolve_openings(cfg, mesh)
        supplies = [item for item in patches if item["role"] == "supply"]
        self.assertEqual(len(supplies), 1)
        self.assertEqual(supplies[0]["type"], "round")

    def test_resolved_opening_preserves_dxf_override_marker(self):
        """A reviewed DXF terminal keeps its override state in case patches."""
        cfg = copy.deepcopy(BASE_CFG)
        cfg["openings"] = [{
            "role": "supply", "type": "round", "wall": "ceiling",
            "cx": 1.0, "cy": 1.0, "w": 0.5, "h": 0.5, "cmh": 400,
            "source_id": "equipment_DXF_SA_01",
            "source_type": "user_confirmed",
            "override_of_dxf": True,
        }, {
            "role": "exhaust", "type": "grille", "wall": "ceiling",
            "cx": 3.0, "cy": 2.0, "w": 0.5, "h": 0.5, "cmh": 400,
        }]
        _, mesh = cfd_export.gen_blockmesh(
            cfg, {key: "wall" for key in cfd_export._FACES},
        )

        patches = cfd_export.resolve_openings(cfg, mesh)

        supply = next(item for item in patches if item["role"] == "supply")
        self.assertTrue(supply["override_of_dxf"])


class GeometrySafetyTests(unittest.TestCase):
    def test_air_terminals_are_not_confused_with_solid_equipment(self):
        geom = {
            "elements": {
                "wall": [{"points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]}],
                "equipment": [
                    {"block_name": "SA_DIFFUSER_01", "points": [[900, 900], [1100, 900],
                                                                    [1100, 1100], [900, 1100]]},
                    {"block_name": "PUMP_01", "points": [[1900, 900], [2300, 900],
                                                             [2300, 1300], [1900, 1300]]},
                ],
                "column": [],
            }
        }
        terminals = cfd_export.diffusers_from_geometry(geom, bbox=[0, 0, 4000, 3000])
        obstacles = cfd_export.obstacles_from_geometry(geom, bbox=[0, 0, 4000, 3000])
        self.assertEqual([d["name"] for d in terminals], ["SA_DIFFUSER_01"])
        self.assertEqual([o["name"] for o in obstacles["obstacles"]], ["PUMP_01"])

    def test_unconfirmed_inferred_ehp_is_not_a_solid_obstacle(self):
        geom = {
            "elements": {
                "wall": [{"points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]}],
                "equipment": [
                    {
                        "layer": "DVM_INDOOR", "confirmed": False,
                        "points": [[500, 500], [1500, 500], [1500, 1200], [500, 1200]],
                        "semantic": {
                            "kind": "equipment", "role": "solid", "needs_review": True,
                            "role_source": "sa_ra_pair_inference",
                            "equipment_type": "ducted_ehp_indoor_unit",
                        },
                    },
                    {
                        "block_name": "PUMP_01", "confirmed": True,
                        "points": [[2000, 500], [2500, 500], [2500, 1200], [2000, 1200]],
                    },
                ],
                "column": [],
            }
        }
        obstacles = cfd_export.obstacles_from_geometry(
            geom, bbox=[0, 0, 4000, 3000],
        )
        self.assertEqual([item["name"] for item in obstacles["obstacles"]], ["PUMP_01"])

    def test_confirmed_heat_source_keeps_dxf_identity_and_heat_evidence(self):
        geom = {
            "elements": {
                "wall": [{"points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]}],
                "equipment": [{
                    "id": "equipment_DXF_EHP_01", "confirmed": True,
                    "block_name": "DVM_INDOOR_01",
                    "points": [[1800, 800], [2600, 800], [2600, 1400], [1800, 1400]],
                    "source_ref": {"handle": "A1", "layer": "DVM_INDOOR"},
                    "semantic": {
                        "kind": "equipment", "role": "heat_source",
                        "height_mm": 900, "power_kw": 5.0,
                        "convective_fraction": 0.8,
                        "evidence": "equipment_schedule:M03-001",
                        "source_type": "user_confirmed",
                    },
                }],
                "column": [],
            }
        }

        obstacles = cfd_export.obstacles_from_geometry(
            geom, bbox=[0, 0, 4000, 3000],
        )

        self.assertEqual(len(obstacles["obstacles"]), 1)
        item = obstacles["obstacles"][0]
        self.assertEqual(item["source_id"], "equipment_DXF_EHP_01")
        self.assertEqual(item["source_label"], "DVM_INDOOR_01")
        self.assertEqual(item["kw"], 5.0)
        self.assertEqual(item["convective_fraction"], 0.8)
        self.assertEqual(item["evidence"], "equipment_schedule:M03-001")

    def test_missing_heat_source_type_stays_unreviewed_in_v3_adapter(self):
        """A raw confirmed DXF heat candidate must not become V3a load input."""
        geom = {
            "elements": {
                "wall": [{"points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]}],
                "equipment": [{
                    "id": "equipment_DXF_EHP_01", "confirmed": True,
                    "block_name": "DVM_INDOOR_01",
                    "points": [[1800, 800], [2600, 800], [2600, 1400], [1800, 1400]],
                    "source_ref": {"handle": "A1", "layer": "DVM_INDOOR"},
                    "semantic": {
                        "kind": "equipment", "role": "heat_source",
                        "height_mm": 900, "power_kw": 5.0,
                        "convective_fraction": 0.8,
                        "evidence": "equipment_schedule:M03-001",
                    },
                }],
                "column": [],
            }
        }

        item = cfd_export.obstacles_from_geometry(
            geom, bbox=[0, 0, 4000, 3000],
        )["obstacles"][0]

        self.assertNotIn("input_power_w", item)
        self.assertNotIn("kw", item)
        self.assertTrue(item["heat_input_needs_review"])

    def test_dxf_detected_heat_source_stays_unreviewed_in_v3_adapter(self):
        """DXF detection is geometry evidence, not a reviewed thermal input."""
        geom = {
            "elements": {
                "wall": [{"points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]}],
                "equipment": [{
                    "id": "equipment_DXF_EHP_01", "confirmed": True,
                    "block_name": "DVM_INDOOR_01",
                    "points": [[1800, 800], [2600, 800], [2600, 1400], [1800, 1400]],
                    "source_ref": {"handle": "A1", "layer": "DVM_INDOOR"},
                    "semantic": {
                        "kind": "equipment", "role": "heat_source",
                        "height_mm": 900, "power_kw": 5.0,
                        "convective_fraction": 0.8,
                        "evidence": "equipment_schedule:M03-001",
                        "source_type": "dxf_detected",
                    },
                }],
                "column": [],
            }
        }

        item = cfd_export.obstacles_from_geometry(
            geom, bbox=[0, 0, 4000, 3000],
        )["obstacles"][0]

        self.assertNotIn("input_power_w", item)
        self.assertNotIn("kw", item)
        self.assertTrue(item["heat_input_needs_review"])

    def test_confirmed_w_based_heat_source_survives_geometry_to_v3_adapter(self):
        """A canonical W-only geometry source must not become a solid-only V3 item."""
        geom = {
            "elements": {
                "wall": [{"points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]}],
                "equipment": [{
                    "id": "equipment_DXF_EHP_W_01", "confirmed": True,
                    "block_name": "DVM_INDOOR_W_01",
                    "points": [[1800, 800], [2600, 800], [2600, 1400], [1800, 1400]],
                    "source_ref": {"handle": "A1-W", "layer": "DVM_INDOOR"},
                    "semantic": {
                        "kind": "equipment", "role": "heat_source",
                        "height_mm": 900, "input_power_w": 5000.0,
                        "convective_fraction": 0.8,
                        "evidence": "equipment_schedule:M03-001",
                        "source_type": "user_confirmed",
                    },
                }],
                "column": [],
            }
        }

        obstacles = cfd_export.obstacles_from_geometry(
            geom, bbox=[0, 0, 4000, 3000],
        )

        item = obstacles["obstacles"][0]
        self.assertEqual(item["input_power_w"], 5000.0)
        self.assertEqual(item["kw"], 5.0)
        self.assertEqual(item["source_type"], "user_confirmed")

    def test_circle_terminal_preserves_explicit_review_semantics(self):
        geom = {
            "elements": {
                "wall": [{"points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]}],
                "equipment": [{
                    "kind": "circle", "center": [1000, 1200], "radius": 100,
                    "layer": "HFB-101",
                    "semantic": {
                        "kind": "air_terminal", "role": "unresolved",
                        "suggested_role": "supply", "terminal_type": "round",
                        "diameter_mm": 200,
                    },
                }],
                "column": [],
            }
        }
        terminals = cfd_export.diffusers_from_geometry(
            geom, bbox=[0, 0, 4000, 3000],
        )
        self.assertEqual(len(terminals), 1)
        self.assertEqual(terminals[0]["cx"], 1.0)
        self.assertEqual(terminals[0]["cy"], 1.2)
        self.assertEqual(terminals[0]["w"], 0.2)
        self.assertEqual(terminals[0]["h"], 0.2)
        self.assertEqual(terminals[0]["role"], "unresolved")
        self.assertEqual(terminals[0]["suggested_role"], "supply")
        self.assertTrue(terminals[0]["requires_role_review"])

    def test_drawing_terminal_preserves_dxf_traceability(self):
        geom = {
            "elements": {
                "wall": [{"points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]}],
                "equipment": [{
                    "id": "equipment_DXF_SA_01",
                    "kind": "circle", "center": [1000, 1200], "radius": 100,
                    "source_ref": {
                        "handle": "A1", "layer": "M-DUCT-SUPPLY",
                        "block_name": "SA_ROUND_01", "entity_type": "INSERT",
                    },
                    "semantic": {
                        "kind": "air_terminal", "role": "supply",
                        "terminal_type": "round",
                        "source_type": "user_confirmed",
                        "override_of_dxf": True,
                    },
                }],
                "column": [],
            }
        }

        terminals = cfd_export.diffusers_from_geometry(
            geom, bbox=[0, 0, 4000, 3000],
        )

        self.assertEqual(len(terminals), 1)
        item = terminals[0]
        self.assertEqual(item["source_id"], "equipment_DXF_SA_01")
        self.assertEqual(item["source_label"], "SA_ROUND_01")
        self.assertEqual(item["source_ref"]["handle"], "A1")
        self.assertEqual(item["source_ref"]["layer"], "M-DUCT-SUPPLY")
        self.assertEqual(item["source_type"], "user_confirmed")
        self.assertTrue(item["override_of_dxf"])

    def test_obstacle_label_falls_back_to_source_reference(self):
        geom = {
            "elements": {
                "wall": [{"points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]}],
                "equipment": [{
                    "id": "equipment_DXF_EHP_01", "confirmed": True,
                    "points": [[1800, 800], [2600, 800], [2600, 1400], [1800, 1400]],
                    "source_ref": {
                        "handle": "A2", "layer": "M-EQUIP",
                        "block_name": "DVM_INDOOR_01",
                    },
                }],
                "column": [],
            }
        }

        obstacles = cfd_export.obstacles_from_geometry(
            geom, bbox=[0, 0, 4000, 3000],
        )

        self.assertEqual(obstacles["obstacles"][0]["name"], "DVM_INDOOR_01")
        self.assertEqual(obstacles["obstacles"][0]["source_label"], "DVM_INDOOR_01")

    def test_subcell_obstacle_is_rejected_instead_of_losing_heat(self):
        cfg = copy.deepcopy(BASE_CFG)
        cfg["room"] = {"L": 2.0, "W": 2.0, "H": 1.0}
        cfg["mesh"] = {"cell": 1.0}
        cfg["obstacles"] = [
            {"kind": "equipment", "bbox": [0.05, 0.05, 0.1, 0.1], "h": 0.5, "kw": 5.0}
        ]
        mesh = {"nx": 2, "ny": 2, "nz": 1, "cells": 4}
        with self.assertRaisesRegex(SystemExit, "한 셀도 차지하지"):
            cfd_export.solid_labels(cfg, mesh)

    def test_v3_total_heat_excludes_solid_cells(self):
        cfg = copy.deepcopy(BASE_CFG)
        cfg["room"] = {"L": 2.0, "W": 2.0, "H": 1.0}
        cfg["mesh"] = {"cell": 1.0}
        cfg["room_polygon"] = [[0, 0], [2, 0], [2, 1], [1, 1], [1, 2], [0, 2]]
        mesh = {"nx": 2, "ny": 2, "nz": 1, "cells": 4}
        labels = cfd_export.solid_labels(cfg, mesh)
        self.assertTrue(labels["solid"])
        self.assertTrue(labels["heat_fluid"])
        self.assertTrue(set(labels["solid"]).isdisjoint(labels["heat_fluid"]))
        topo = cfd_export.gen_toposet_zones(cfg, labels)
        self.assertIn("name heatCells", topo)
        self.assertIn("source labelToCell", topo)

    def test_every_opening_face_cell_must_be_fluid(self):
        cfg = copy.deepcopy(BASE_CFG)
        cfg["room"] = {"L": 2.0, "W": 2.0, "H": 1.0}
        cfg["mesh"] = {"cell": 0.5}
        cfg["room_polygon"] = [[0, 0], [2, 0], [2, 1], [0, 1]]
        mesh = {"nx": 4, "ny": 4, "nz": 2, "cells": 32}
        labels = cfd_export.solid_labels(cfg, mesh)
        # On x0, u=y and v=z.  This patch straddles y=1: its centre can look
        # plausible while half of its adjacent cells are outside the room.
        patch = {"name": "sup0", "wall": "x0", "uax": "y", "vax": "z",
                 "rect_snap": [0.5, 0.0, 1.5, 1.0]}
        with self.assertRaisesRegex(SystemExit, "개 셀이 고체"):
            cfd_export.validate_openings_fluid(cfg, [patch], labels, mesh)


class CasePublishingTests(unittest.TestCase):
    def _temp_parent(self):
        repo = Path(__file__).resolve().parents[1]
        return tempfile.TemporaryDirectory(prefix=".test-cfd-", dir=repo)

    def test_unknown_existing_directory_is_never_deleted(self):
        with self._temp_parent() as tmp:
            target = Path(tmp) / "important"
            target.mkdir()
            marker = target / "keep.txt"
            marker.write_text("do not delete", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "덮어쓰지"):
                cfd_export.build_case(copy.deepcopy(BASE_CFG), target)
            self.assertEqual(marker.read_text(encoding="utf-8"), "do not delete")

    def test_failed_build_leaves_no_half_case_and_can_retry(self):
        with self._temp_parent() as tmp:
            target = Path(tmp) / "case"
            bad = copy.deepcopy(BASE_CFG)
            bad["openings"] = [
                {"role": "supply", "type": "grille", "wall": "ceiling",
                 "cx": 1.0, "cy": 1.0, "w": 0.5, "h": 0.5, "cmh": 400}
            ]
            with self.assertRaisesRegex(SystemExit, "exhaust"):
                cfd_export.build_case(bad, target)
            self.assertFalse(target.exists())
            self.assertFalse(list(Path(tmp).glob(".case.building-*")))
            cfd_export.build_case(copy.deepcopy(BASE_CFG), target)
            self.assertTrue((target / "cfd_case_meta.json").is_file())


class RunnerSafetyTests(unittest.TestCase):
    def test_latest_restart_time_uses_highest_numeric_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("0", "10", "2.5", "postProcessing"):
                (root / name).mkdir()
            self.assertEqual(cfd_run._latest_local_time_name(root), "10")

    def test_restart_staging_copies_only_zero_and_latest_time(self):
        command = cfd_run._stage_case_command(
            "/mnt/c/MEP CFD/case", "~/cfd_runs/safe_case", "401.8944"
        )
        self.assertIn("! -regex '.*/[0-9]+(\\.[0-9]+)?'", command)
        self.assertIn("'/mnt/c/MEP CFD/case'/0", command)
        self.assertIn("'/mnt/c/MEP CFD/case'/401.8944", command)
        self.assertNotIn("cp -r '/mnt/c/MEP CFD/case'", command)
        self.assertIn("rm -rf ~/cfd_runs/safe_case", command)
        self.assertIn("! -name postProcessing", command)
        self.assertIn("! -name VTK", command)
        self.assertIn("! -name 'log.*'", command)

    def test_restart_staging_writes_a_valid_recovery_fingerprint(self):
        fingerprint = "a" * 64
        command = cfd_run._stage_case_command(
            "/mnt/c/project/case", "~/cfd_runs/safe_case", "10.0",
            fingerprint,
        )
        self.assertIn(".mep_cfd_resume_fingerprint", command)
        self.assertIn(fingerprint, command)
        with self.assertRaisesRegex(ValueError, "지문"):
            cfd_run._stage_case_command(
                "/mnt/c/project/case", "~/cfd_runs/safe_case", "10.0",
                "not-a-fingerprint",
            )

    def test_restart_fingerprint_changes_with_saved_field_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp)
            (case / "system").mkdir()
            (case / "10").mkdir()
            (case / "Allrun").write_text("run", encoding="ascii")
            (case / "system" / "controlDict").write_text("control", encoding="ascii")
            (case / "10" / "U").write_text("velocity", encoding="ascii")
            field = case / "10" / "T"
            field.write_text("first", encoding="ascii")
            first = cfd_run._restart_fingerprint(case, "10")
            field.write_text("second", encoding="ascii")
            second = cfd_run._restart_fingerprint(case, "10")
        self.assertNotEqual(first, second)

    def test_restart_fingerprint_ignores_only_restart_audit_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp)
            (case / "system").mkdir()
            (case / "10").mkdir()
            (case / "Allrun").write_text("run", encoding="ascii")
            (case / "system" / "controlDict").write_text("control", encoding="ascii")
            (case / "10" / "U").write_text("velocity", encoding="ascii")
            (case / "10" / "T").write_text("temperature", encoding="ascii")
            restart = case / "thermal_restart_input.json"
            restart.write_text(json.dumps({
                "created_at": "first", "duration_s": 2.0, "settings": {"maxCo": 1.0},
            }), encoding="utf-8")
            first = cfd_run._restart_fingerprint(case, "10")
            restart.write_text(json.dumps({
                "created_at": "second", "duration_s": 2.0, "settings": {"maxCo": 1.0},
            }), encoding="utf-8")
            second = cfd_run._restart_fingerprint(case, "10")
            restart.write_text(json.dumps({
                "created_at": "second", "duration_s": 3.0, "settings": {"maxCo": 1.0},
            }), encoding="utf-8")
            changed = cfd_run._restart_fingerprint(case, "10")
        self.assertEqual(first, second)
        self.assertNotEqual(second, changed)

    def test_restart_fingerprint_requires_core_inputs_and_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp)
            (case / "10").mkdir()
            self.assertIsNone(cfd_run._restart_fingerprint(case, "10"))
            (case / "system").mkdir()
            (case / "Allrun").write_text("run", encoding="ascii")
            (case / "system" / "controlDict").write_text("control", encoding="ascii")
            (case / "10" / "T").write_text("temperature", encoding="ascii")
            self.assertIsNone(cfd_run._restart_fingerprint(case, "10"))

    def test_remote_checkpoint_probe_distinguishes_active_and_recoverable(self):
        active = mock.Mock(returncode=0, stdout="ACTIVE\n", stderr="")
        recovered = mock.Mock(returncode=0, stdout="RECOVERABLE 12.5\n", stderr="")
        with mock.patch.object(cfd_run, "_wsl", return_value=active):
            self.assertEqual(
                cfd_run._remote_checkpoint_status(
                    "~/cfd_runs/safe_case", "b" * 64, 10.0
                )["status"],
                "active",
            )
        with mock.patch.object(cfd_run, "_wsl", return_value=recovered) as wsl:
            result = cfd_run._remote_checkpoint_status(
                "~/cfd_runs/safe_case", "b" * 64, 10.0
            )
            command = wsl.call_args.args[0]
        self.assertEqual(result["status"], "recoverable")
        self.assertEqual(result["latest_time"], 12.5)
        self.assertIn("pgrep -f '[b]uoyantBoussinesqPimpleFoam'", command)
        self.assertIn("/proc/$p/cwd", command)

    def test_remote_checkpoint_probe_timeout_fails_closed(self):
        with mock.patch.object(
            cfd_run,
            "_wsl",
            side_effect=cfd_run.subprocess.TimeoutExpired(["wsl"], 20),
        ):
            result = cfd_run._remote_checkpoint_status(
                "~/cfd_runs/safe_case", "b" * 64, 10.0
            )
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("20초", result["error"])

    def test_remote_checkpoint_probe_blocks_mismatched_newer_checkpoint(self):
        conflict = mock.Mock(returncode=0, stdout="CONFLICT 12.5\n", stderr="")
        with mock.patch.object(cfd_run, "_wsl", return_value=conflict) as wsl:
            result = cfd_run._remote_checkpoint_status(
                "~/cfd_runs/safe_case", "b" * 64, 10.0
            )
            command = wsl.call_args.args[0]
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(result["latest_time"], 12.5)
        self.assertLess(command.index("pgrep -f"), command.index("marker=$(cat"))

    def test_run_case_reuses_verified_remote_checkpoint_without_restaging(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp)
            (case / "10").mkdir()
            with mock.patch.object(cfd_run, "diagnose_openfoam", return_value={
                "ok": True, "distro": "Ubuntu-24.04", "bashrc": "/of/bashrc",
            }), mock.patch.object(
                cfd_run, "win_to_wsl", return_value="/mnt/c/case"
            ), mock.patch.object(
                cfd_run, "_restart_fingerprint", return_value="a" * 64
            ), mock.patch.object(
                cfd_run, "_remote_checkpoint_status",
                return_value={"status": "recoverable", "latest_time": 12.5},
            ), mock.patch.object(
                cfd_run, "_stage_case_command"
            ) as stage, mock.patch.object(
                cfd_run.subprocess, "Popen", side_effect=RuntimeError("stop after staging")
            ):
                with self.assertRaisesRegex(RuntimeError, "stop after staging"):
                    cfd_run.run_case(case, restart_from_latest=True)
        stage.assert_not_called()

    def test_run_case_rejects_duplicate_solver_in_same_remote_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp)
            (case / "10").mkdir()
            with mock.patch.object(cfd_run, "diagnose_openfoam", return_value={
                "ok": True, "distro": "Ubuntu-24.04", "bashrc": "/of/bashrc",
            }), mock.patch.object(
                cfd_run, "win_to_wsl", return_value="/mnt/c/case"
            ), mock.patch.object(
                cfd_run, "_restart_fingerprint", return_value="a" * 64
            ), mock.patch.object(
                cfd_run, "_remote_checkpoint_status",
                return_value={"status": "active"},
            ), mock.patch.object(cfd_run.subprocess, "Popen") as popen:
                result = cfd_run.run_case(case, restart_from_latest=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "WSL_REMOTE_SOLVER_ACTIVE")
        popen.assert_not_called()

    def test_run_case_does_not_restage_when_remote_probe_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp)
            (case / "10").mkdir()
            with mock.patch.object(cfd_run, "diagnose_openfoam", return_value={
                "ok": True, "distro": "Ubuntu-24.04", "bashrc": "/of/bashrc",
            }), mock.patch.object(
                cfd_run, "win_to_wsl", return_value="/mnt/c/case"
            ), mock.patch.object(
                cfd_run, "_restart_fingerprint", return_value="a" * 64
            ), mock.patch.object(
                cfd_run, "_remote_checkpoint_status", return_value={
                    "status": "unavailable", "error": "probe timed out",
                }
            ), mock.patch.object(
                cfd_run, "_stage_case_command"
            ) as stage, mock.patch.object(cfd_run.subprocess, "Popen") as popen:
                result = cfd_run.run_case(case, restart_from_latest=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "WSL_REMOTE_CHECKPOINT_PROBE_FAILED")
        self.assertIn("probe timed out", result["error"])
        stage.assert_not_called()
        popen.assert_not_called()

    def test_run_case_does_not_restage_mismatched_newer_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp)
            (case / "10").mkdir()
            with mock.patch.object(cfd_run, "diagnose_openfoam", return_value={
                "ok": True, "distro": "Ubuntu-24.04", "bashrc": "/of/bashrc",
            }), mock.patch.object(
                cfd_run, "win_to_wsl", return_value="/mnt/c/case"
            ), mock.patch.object(
                cfd_run, "_restart_fingerprint", return_value="a" * 64
            ), mock.patch.object(
                cfd_run, "_remote_checkpoint_status", return_value={
                    "status": "conflict", "latest_time": 12.5,
                    "error": "different input",
                }
            ), mock.patch.object(
                cfd_run, "_stage_case_command"
            ) as stage, mock.patch.object(cfd_run.subprocess, "Popen") as popen:
                result = cfd_run.run_case(case, restart_from_latest=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "WSL_REMOTE_CHECKPOINT_CONFLICT")
        self.assertIn("12.5s", result["error"])
        stage.assert_not_called()
        popen.assert_not_called()

    def test_run_case_does_not_restage_without_a_restart_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp)
            (case / "10").mkdir()
            with mock.patch.object(cfd_run, "diagnose_openfoam", return_value={
                "ok": True, "distro": "Ubuntu-24.04", "bashrc": "/of/bashrc",
            }), mock.patch.object(
                cfd_run, "win_to_wsl", return_value="/mnt/c/case"
            ), mock.patch.object(
                cfd_run, "_restart_fingerprint", return_value=None
            ), mock.patch.object(
                cfd_run, "_remote_checkpoint_status"
            ) as probe, mock.patch.object(
                cfd_run, "_stage_case_command"
            ) as stage, mock.patch.object(cfd_run.subprocess, "Popen") as popen:
                result = cfd_run.run_case(case, restart_from_latest=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "WSL_RESTART_FINGERPRINT_UNAVAILABLE")
        probe.assert_not_called()
        stage.assert_not_called()
        popen.assert_not_called()

    def test_fresh_staging_does_not_copy_any_positive_time(self):
        command = cfd_run._stage_case_command(
            "/mnt/c/project/case", "~/cfd_runs/safe_case"
        )
        self.assertIn("/case/0", command)
        self.assertNotIn("401.8944", command)

    def test_staging_rejects_unsafe_remote_directory(self):
        with self.assertRaisesRegex(ValueError, "안전한"):
            cfd_run._stage_case_command(
                "/mnt/c/project/case", "~/cfd_runs/safe; touch /tmp/injected"
            )

    def _probe(self, distro, *, version="v1912", bashrc=True, commands=True):
        return {
            "wsl_available": True,
            "returncode": 0,
            "error": "",
            "distro": distro,
            "bashrc": "/usr/share/openfoam/etc/bashrc" if bashrc else "",
            "version": version,
            "package_version": "1912.200626-2build3",
            "commands": ({name: f"/usr/bin/{name}" for name in cfd_run.ALL_OPENFOAM_COMMANDS}
                         if commands else {}),
        }

    def test_environment_probe_selects_compatible_non_default_distro(self):
        def probe(distro=None):
            if distro is None:
                return self._probe("OtherLinux", bashrc=False, commands=False)
            return self._probe(distro)

        with mock.patch.object(cfd_run, "_list_wsl_distros",
                               return_value=["OtherLinux", "Ubuntu-24.04"]), \
             mock.patch.object(cfd_run, "_probe_openfoam", side_effect=probe):
            result = cfd_run.diagnose_openfoam()

        self.assertTrue(result["ok"])
        self.assertEqual(result["distro"], "Ubuntu-24.04")
        self.assertEqual(result["selection"], "automatic")
        self.assertTrue(result["body_fitted_ready"])

    def test_environment_probe_accepts_current_openfoam_profile(self):
        with mock.patch.object(cfd_run, "_list_wsl_distros", return_value=["Ubuntu"]), \
             mock.patch.object(cfd_run, "_probe_openfoam",
                               return_value=self._probe("Ubuntu", version="v2606")):
            result = cfd_run.diagnose_openfoam("Ubuntu")

        self.assertTrue(result["ok"])
        self.assertEqual(result["compatible_profile"], "openfoam-v2606")
        self.assertTrue(result["thermal_detailed_ready"])

    def test_environment_probe_keeps_v1912_as_legacy_profile(self):
        result = cfd_run._capability_result(
            self._probe("Ubuntu", version="v1912"), ["Ubuntu"], "configured"
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["compatible_profile"], "openfoam-v1912-legacy")
        self.assertFalse(result["thermal_detailed_ready"])

    def test_missing_openfoam_points_novice_to_one_click_installer(self):
        probe = self._probe("Ubuntu-24.04", version="")
        probe["bashrc"] = ""
        probe["commands"] = {}
        result = cfd_run._capability_result(
            probe, ["Ubuntu-24.04"], "automatic"
        )
        self.assertEqual(result["status"], "openfoam_missing")
        self.assertIn("install_openfoam2606.bat", result["fix"])

    def test_utf16_wsl_distribution_listing_is_decoded(self):
        raw = "Ubuntu-24.04\r\nUbuntu\r\n".encode("utf-16-le")
        self.assertIn("Ubuntu-24.04", cfd_run._decode_wsl_listing(raw))

    def test_remote_directory_is_shell_safe_for_korean_and_spaces(self):
        path = cfd_run._remote_run_dir(r"C:\프로젝트\전기실 A", "전기실 A - 1층")
        self.assertRegex(path, r"^~/cfd_runs/[A-Za-z0-9_.-]+$")
        self.assertNotIn(" ", path)

    def test_allrun_propagates_mesh_and_solver_failures(self):
        script = cfd_export.gen_allrun(True, True, True)
        self.assertTrue(script.startswith("#!/bin/bash\nset -o pipefail"))
        self.assertIn("checkMesh FAILED", script)
        self.assertIn("PIPESTATUS[0]", script)

    def test_sparse_recovery_selection_always_includes_latest_time(self):
        # The previous NR % 3 shell sampling omitted the newest directory when
        # the recent window contained six entries.
        selected = cfd_run._select_recovery_times(
            ["600", "100", "500", "200", "400", "300"]
        )
        self.assertEqual(selected, ["100", "200", "300", "400", "500", "600"])
        self.assertEqual(selected[-1], "600")

    def test_recovery_keeps_anchors_and_dense_recent_tail(self):
        names = [str(value) for value in range(2, 62, 2)]
        selected = cfd_run._select_recovery_times(names, limit=7)
        self.assertEqual(len(selected), 7)
        self.assertEqual(selected[0], "2")
        self.assertIn("30", selected)
        self.assertEqual(selected[-5:], ["52", "54", "56", "58", "60"])

    def test_result_recovery_includes_mapping_audit_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "log.mapFields").write_text("Source time: 30\n", encoding="utf-8")
            (root / "log.simpleFoam").write_text("End\n", encoding="utf-8")
            self.assertIn("log.mapFields", cfd_run._result_relpaths(root))

    def test_result_recovery_includes_body_fitted_vtk_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "VTK").mkdir()
            self.assertIn("VTK", cfd_run._result_relpaths(root))

    def test_failure_log_publish_does_not_remove_old_result_times(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix=".test-failure-log-", dir=repo) as tmp:
            case = Path(tmp) / "case"
            stage = Path(tmp) / "stage"
            case.mkdir()
            stage.mkdir()
            (case / "100").mkdir()
            (case / "log.simpleFoam").write_text("old", encoding="ascii")
            (stage / "log.simpleFoam").write_text("new", encoding="ascii")
            published = cfd_run._publish_log_stage(str(stage), str(case))
            content = (case / "log.simpleFoam").read_text(encoding="ascii")
            old_time_preserved = (case / "100").is_dir()
        self.assertEqual(published, ["log.simpleFoam"])
        self.assertEqual(content, "new")
        self.assertTrue(old_time_preserved)

    def test_verified_recovery_replaces_stale_result_set(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix=".test-recovery-", dir=repo) as tmp:
            case = Path(tmp) / "case"
            stage = Path(tmp) / "stage"
            case.mkdir()
            stage.mkdir()
            (case / "0").mkdir()
            (case / "100").mkdir()
            (case / "900").mkdir()
            (case / "100" / "T").write_text("old", encoding="ascii")
            (case / "900" / "T").write_text("stale-latest", encoding="ascii")
            (case / "log.oldFoam").write_text("old", encoding="ascii")
            (case / ".mep_cfd_last_recovery").write_text("old", encoding="ascii")

            (stage / "100").mkdir()
            (stage / "300").mkdir()
            (stage / "100" / "T").write_text("new-100", encoding="ascii")
            (stage / "300" / "T").write_text("new-latest", encoding="ascii")
            (stage / "log.newFoam").write_text("new", encoding="ascii")
            (stage / ".mep_cfd_last_recovery").write_text("token", encoding="ascii")

            cfd_run._publish_recovery(str(stage), str(case))

            self.assertTrue((case / "0").is_dir())
            self.assertFalse((case / "900").exists())
            self.assertFalse((case / "log.oldFoam").exists())
            self.assertEqual((case / "100" / "T").read_text(encoding="ascii"), "new-100")
            self.assertEqual((case / "300" / "T").read_text(encoding="ascii"), "new-latest")
            self.assertEqual((case / ".mep_cfd_last_recovery").read_text(encoding="ascii"), "token")

    def test_continuation_recovery_preserves_bounded_time_window(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix=".test-window-recovery-", dir=repo) as tmp:
            case = Path(tmp) / "case"
            stage = Path(tmp) / "stage"
            case.mkdir()
            stage.mkdir()
            (case / "0").mkdir()
            for name in ("100", "200"):
                (case / name).mkdir()
                (case / name / "T").write_text("old-" + name, encoding="ascii")
            for name in ("300", "400"):
                (stage / name).mkdir()
                (stage / name / "T").write_text("new-" + name, encoding="ascii")
            (stage / "log.newFoam").write_text("new", encoding="ascii")

            cfd_run._publish_recovery(
                str(stage), str(case), preserve_time_dirs=3
            )

            self.assertTrue((case / "0").is_dir())
            self.assertFalse((case / "100").exists())
            self.assertEqual((case / "200" / "T").read_text(encoding="ascii"), "old-200")
            self.assertEqual((case / "300" / "T").read_text(encoding="ascii"), "new-300")
            self.assertEqual((case / "400" / "T").read_text(encoding="ascii"), "new-400")


class ConvergenceBadgeTests(unittest.TestCase):
    def _parsed(self, *, continuity=1e-5, residual=1e-4, crashed=False):
        return {
            "crashed": crashed,
            "continuity_global": [(400, continuity)],
            "residuals": {
                field: [1e-1, residual]
                for field in ("Ux", "Uy", "Uz", "p_rgh", "T", "k", "epsilon")
            },
        }

    def test_energy_closure_alone_is_not_called_converged(self):
        badge, color = cfd_report.convergence_badge(
            self._parsed(continuity=0.1, residual=0.1),
            {"closure_pct": 100.0, "closure_osc": 1.0, "mass_err_pct": 0.0,
             "T_avg_C": 25.0, "T_max_C": 26.0, "U_max": 0.4},
        )
        self.assertIn("추가확인", badge)
        self.assertEqual(color, "#b9770e")

    def test_green_requires_all_available_gates(self):
        badge, color = cfd_report.convergence_badge(
            self._parsed(),
            {"closure_pct": 99.0, "closure_osc": 2.0, "mass_err_pct": 1.0,
             "T_avg_C": 25.0, "T_max_C": 26.0, "U_max": 0.4},
        )
        self.assertTrue(badge.startswith("수렴"))
        self.assertEqual(color, "#1e8449")

    def test_v3_report_renders_model_limit_without_name_error(self):
        meta = {
            "config": {"name": "v3", "room": {"L": 2, "W": 2, "H": 1},
                       "mesh": {"cell": 1}, "heat": {}, "inlet": {"T": 293.15}},
            "mesh": {"nx": 2, "ny": 2, "nz": 1, "cells": 4},
            "heat": {"mode": "none"},
            "model_quality": {"warning": "다공성 예비 모델"},
        }
        parsed = {"n_iters": 1, "crashed": False, "residuals": {},
                  "continuity_global": [], "rho_min": [], "bounding": []}
        with tempfile.TemporaryDirectory(prefix=".test-report-",
                                         dir=Path(__file__).resolve().parents[1]) as tmp:
            out = Path(tmp) / "report.html"
            cfd_report.build_html_report(tmp, meta, parsed, None, None, {}, out)
            text = out.read_text(encoding="utf-8")
        self.assertIn("형상 모델 한계", text)
        self.assertIn("다공성 예비 모델", text)

    def test_v3_report_separates_input_convective_and_unmodelled_radiative_heat(self):
        meta = {
            "config": {"name": "v3-heat", "room": {"L": 2, "W": 2, "H": 1},
                       "mesh": {"cell": 1}, "heat": {}, "inlet": {"T": 293.15}},
            "mesh": {"nx": 2, "ny": 2, "nz": 1, "cells": 4},
            "heat": {"mode": "volume", "power_w": 4000.0,
                     "input_power_w": 5000.0,
                     "applied_convective_power_w": 4000.0,
                     "excluded_radiative_power_w": 1000.0,
                     "via": "obstacles"},
            "equip_zones": [{
                "source_id": "DXF-EHP-01", "source_label": "EHP 실내기 1",
                "input_power_w": 5000.0, "convective_power_w": 4000.0,
                "evidence": "equipment_schedule:M03-001",
            }],
            "model_quality": {"warning": "다공성 예비 모델"},
        }
        parsed = {"n_iters": 1, "crashed": False, "residuals": {},
                  "continuity_global": [], "rho_min": [], "bounding": []}
        with tempfile.TemporaryDirectory(prefix=".test-report-",
                                         dir=Path(__file__).resolve().parents[1]) as tmp:
            out = Path(tmp) / "report.html"
            cfd_report.build_html_report(tmp, meta, parsed, None, None,
                                         {"heat_kw": 4.0}, out)
            text = out.read_text(encoding="utf-8")
        self.assertIn("입력 5.0 kW", text)
        self.assertIn("CFD 대류 주입 4.0 kW", text)
        self.assertIn("미모델 복사 1.0 kW", text)
        self.assertIn("DXF-EHP-01", text)
        self.assertIn("equipment_schedule:M03-001", text)


if __name__ == "__main__":
    unittest.main()
