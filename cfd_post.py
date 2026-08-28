"""Order-independent post-processing for body-fitted OpenFOAM VTU results."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from numbers import Real
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET

import cfd_numerical_sensitivity_job as sensitivity_job


def _now():
    return datetime.now(timezone.utc).isoformat()


def _tag(element):
    return element.tag.rsplit("}", 1)[-1]


def _child(parent, name):
    return next((item for item in parent if _tag(item) == name), None)


def _data_array(parent, name):
    for item in (() if parent is None else parent):
        if _tag(item) == "DataArray" and item.attrib.get("Name") == name:
            if item.attrib.get("format") != "ascii":
                raise ValueError(f"VTU array is not ASCII: {name}")
            return item
    raise ValueError(f"VTU DataArray not found: {name}")


def _floats(array):
    return [float(value) for value in (array.text or "").split()]


def _integers(array):
    return [int(value) for value in (array.text or "").split()]


def _triples(values, expected, name):
    if len(values) != expected * 3:
        raise ValueError(f"{name} tuple count mismatch")
    return [values[index:index + 3] for index in range(0, len(values), 3)]


def _percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return None
    position = fraction * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _weighted_percentile(values, weights, fraction):
    """Return the empirical weighted quantile without inventing interpolation."""
    pairs = sorted(zip(values, weights), key=lambda item: item[0])
    total = sum(weight for _, weight in pairs)
    threshold = total * fraction
    cumulative = 0.0
    for value, weight in pairs:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return pairs[-1][0]


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class PostprocessEvidenceError(ValueError):
    """Raised when post-processing evidence is missing, mismatched, or untrusted."""


TRUSTED_EXHAUST_ENERGY_CLOSURE_BASIS = "solver_positive_phi_and_owner_cell_temperature"
TRUSTED_EXHAUST_TEMPERATURE_METHOD = "positive_phi_weighted_owner_cell_temperature"


def _finite_real(value):
    """Return a finite scalar only for real (but not boolean) evidence values."""
    return (
        isinstance(value, Real)
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _require_finite_real(value, code, *, positive=False):
    if not _finite_real(value):
        raise PostprocessEvidenceError(code)
    number = float(value)
    if positive and number <= 0:
        raise PostprocessEvidenceError(code)
    return number


def _normalise_occupied_selector(selector):
    """Validate the shared selector contract and retain its canonical hash."""
    supplied_hash = None
    raw_selector = selector
    if isinstance(selector, dict) and "selector_sha256" in selector:
        raw_selector = dict(selector)
        supplied_hash = raw_selector.pop("selector_sha256")
    try:
        normalised = sensitivity_job.normalize_occupied_volume_band(raw_selector)
    except sensitivity_job.NumericalSensitivityJobInputError as error:
        raise PostprocessEvidenceError(
            f"OCCUPIED_VTU_SELECTOR_INVALID: {error}"
        ) from error
    if supplied_hash is not None and supplied_hash != normalised["selector_sha256"]:
        raise PostprocessEvidenceError(
            "OCCUPIED_VTU_SELECTOR_INVALID: selector_sha256 does not match the "
            "canonical selector")
    return normalised


def _optional_data_array(parent, name):
    for item in (() if parent is None else parent):
        if _tag(item) == "DataArray" and item.attrib.get("Name") == name:
            if item.attrib.get("format") != "ascii":
                raise ValueError(f"VTU array is not ASCII: {name}")
            return item
    return None


def read_internal_vtu(path):
    """Read cell-centred T/U fields and geometric cell centres from ASCII VTU."""
    path = Path(path)
    root = ET.parse(path).getroot()
    if root.attrib.get("type") != "UnstructuredGrid":
        raise ValueError("Expected an UnstructuredGrid VTU file")
    grid = next(item for item in root if _tag(item) == "UnstructuredGrid")
    piece = _child(grid, "Piece")
    if piece is None:
        raise ValueError("VTU Piece not found")
    point_count = int(piece.attrib["NumberOfPoints"])
    cell_count = int(piece.attrib["NumberOfCells"])
    points = _triples(
        _floats(_data_array(_child(piece, "Points"), "Points")),
        point_count,
        "Points",
    )
    cells = _child(piece, "Cells")
    connectivity = _integers(_data_array(cells, "connectivity"))
    offsets = _integers(_data_array(cells, "offsets"))
    if len(offsets) != cell_count or (offsets and offsets[-1] != len(connectivity)):
        raise ValueError("VTU cell connectivity mismatch")
    centres = []
    start = 0
    for end in offsets:
        vertex_ids = connectivity[start:end]
        start = end
        if not vertex_ids:
            raise ValueError("VTU cell has no vertices")
        vertices = [points[index] for index in vertex_ids]
        centres.append([
            sum(vertex[axis] for vertex in vertices) / len(vertices)
            for axis in range(3)
        ])
    cell_data = _child(piece, "CellData")
    temperature = _floats(_data_array(cell_data, "T"))
    velocity = _triples(_floats(_data_array(cell_data, "U")), cell_count, "U")
    if len(temperature) != cell_count:
        raise ValueError("T tuple count mismatch")
    field_data = _child(grid, "FieldData")
    time_values = _floats(_data_array(field_data, "TimeValue")) if field_data is not None else []
    volume_array = _optional_data_array(cell_data, "V")
    volume_m3 = _floats(volume_array) if volume_array is not None else None
    return {
        "source": str(path),
        "time_s": time_values[0] if time_values else None,
        "point_count": point_count,
        "cell_count": cell_count,
        "centres_m": centres,
        "temperature_k": temperature,
        "velocity_m_s": velocity,
        "volume_m3": volume_m3,
    }


def _extreme_row(index, data, speeds):
    return {
        "cell_index": index,
        "centre_m": data["centres_m"][index],
        "temperature_k": data["temperature_k"][index],
        "velocity_m_s": data["velocity_m_s"][index],
        "speed_m_s": speeds[index],
    }


def summarize_vtu(path):
    """Return deterministic statistics and coordinate-selected mid-plane samples."""
    data = read_internal_vtu(path)
    temperatures = data["temperature_k"]
    speeds = [math.sqrt(sum(value * value for value in vector))
              for vector in data["velocity_m_s"]]
    minimum_t = min(range(data["cell_count"]), key=temperatures.__getitem__)
    maximum_t = max(range(data["cell_count"]), key=temperatures.__getitem__)
    maximum_u = max(range(data["cell_count"]), key=speeds.__getitem__)
    bounds = {
        "minimum": [min(row[axis] for row in data["centres_m"]) for axis in range(3)],
        "maximum": [max(row[axis] for row in data["centres_m"]) for axis in range(3)],
    }
    spans = [bounds["maximum"][axis] - bounds["minimum"][axis] for axis in range(3)]
    characteristic_length = (
        max(spans[0] * spans[1] * spans[2], 0.0) / data["cell_count"]
    ) ** (1.0 / 3.0)
    fields = {
        "T": {"components": 1, "unit": "K", "association": "cell"},
        "U": {"components": 3, "unit": "m/s", "association": "cell"},
    }
    if data.get("volume_m3") is not None:
        fields["V"] = {"components": 1, "unit": "m3", "association": "cell"}
    slices = []
    for axis, axis_name in enumerate("xyz"):
        target = 0.5 * (bounds["minimum"][axis] + bounds["maximum"][axis])
        distances = [abs(row[axis] - target) for row in data["centres_m"]]
        band = max(min(distances) + 1e-9, 0.5 * characteristic_length)
        indices = [index for index, distance in enumerate(distances) if distance <= band]
        other_axes = [item for item in range(3) if item != axis]
        indices.sort(key=lambda index: (
            data["centres_m"][index][other_axes[0]],
            data["centres_m"][index][other_axes[1]],
            index,
        ))
        slices.append({
            "axis": axis_name,
            "target_m": target,
            "selection": "cell_centres_within_characteristic_half_band",
            "half_band_m": band,
            "sample_count": len(indices),
            "samples": [_extreme_row(index, data, speeds) for index in indices],
        })
    return {
        "schema_version": 1,
        "contract": "body_fitted_summary.v1",
        "created_at": _now(),
        "source": str(Path(path)),
        "source_sha256": _sha256(path),
        "time_s": data["time_s"],
        "association": "cell",
        "aggregation": "cell_count_unweighted",
        "point_count": data["point_count"],
        "cell_count": data["cell_count"],
        "bounds_m": bounds,
        "fields": fields,
        "temperature": {
            "unit": "K",
            "minimum": min(temperatures),
            "maximum": max(temperatures),
            "mean": sum(temperatures) / len(temperatures),
            "p50": _percentile(temperatures, 0.50),
            "p95": _percentile(temperatures, 0.95),
            "coldest_cell": _extreme_row(minimum_t, data, speeds),
            "hottest_cell": _extreme_row(maximum_t, data, speeds),
        },
        "velocity": {
            "unit": "m/s",
            "minimum_speed": min(speeds),
            "maximum_speed": max(speeds),
            "mean_speed": sum(speeds) / len(speeds),
            "rms_speed": math.sqrt(sum(value * value for value in speeds) / len(speeds)),
            "p50_speed": _percentile(speeds, 0.50),
            "p95_speed": _percentile(speeds, 0.95),
            "peak_cell": _extreme_row(maximum_u, data, speeds),
        },
        "slices": slices,
    }


def _atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", delete=False,
            dir=path.parent, prefix="." + path.name + ".", suffix=".tmp") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def _latest_internal_vtu(case):
    candidates = list((Path(case) / "VTK").glob("**/internal.vtu"))
    if not candidates:
        return None
    timed = []
    for path in candidates:
        try:
            timed.append((read_internal_vtu(path)["time_s"], path))
        except (OSError, ValueError, ET.ParseError):
            continue
    return max(timed, key=lambda item: (float(item[0] or -1), str(item[1])))[1] if timed else None


def build_result_artifacts(case_dir):
    """Write result_manifest.json, summary JSON and three coordinate slices."""
    case = Path(case_dir).expanduser().resolve()
    source = _latest_internal_vtu(case)
    if source is None:
        return {"ok": False, "error": "최종 internal.vtu 결과가 없습니다.", "case": str(case)}
    try:
        summary = summarize_vtu(source)
        results = case / "results"
        slice_refs = []
        for item in summary.pop("slices"):
            relative = Path("results") / "slices" / f"{item['axis']}_mid.json"
            _atomic_json(case / relative, item)
            slice_refs.append({
                "axis": item["axis"], "target_m": item["target_m"],
                "sample_count": item["sample_count"], "path": relative.as_posix(),
                "sha256": _sha256(case / relative),
            })
        summary_path = results / "body_fitted_summary.json"
        _atomic_json(summary_path, summary)
        occupied_qoi_ref = None
        thermal_input_path = case / "thermal_input.json"
        if thermal_input_path.is_file():
            thermal_input = json.loads(thermal_input_path.read_text(encoding="utf-8"))
            settings = thermal_input.get("settings") or {}
            selector = settings.get("occupied_volume_selector")
            if selector is not None:
                occupied_qoi = compute_occupied_volume_qois_from_vtu(
                    source,
                    selector,
                    floor_elevation_m=settings.get("occupied_floor_elevation_m"),
                )
                occupied_qoi_path = results / "occupied_volume_qoi.json"
                _atomic_json(occupied_qoi_path, occupied_qoi)
                occupied_qoi_ref = {
                    "path": occupied_qoi_path.relative_to(case).as_posix(),
                    "sha256": _sha256(occupied_qoi_path),
                    "selector_sha256": occupied_qoi["selector_sha256"],
                }
        manifest = {
            "schema_version": 1,
            "contract": "result_manifest.v1",
            "engine": "body_fitted_openfoam_vtu",
            "created_at": _now(),
            "time_s": summary["time_s"],
            "source": {
                "path": source.relative_to(case).as_posix(),
                "sha256": summary["source_sha256"],
                "format": "VTK XML UnstructuredGrid ASCII",
            },
            "mesh_manifest_sha256": (
                _sha256(case / "mesh_manifest.json")
                if (case / "mesh_manifest.json").is_file() else None
            ),
            "run_manifest_sha256": (
                _sha256(case / "run_manifest.json")
                if (case / "run_manifest.json").is_file() else None
            ),
            "thermal_input_sha256": (
                _sha256(case / "thermal_input.json")
                if (case / "thermal_input.json").is_file() else None
            ),
            "field_location": "cell",
            "fields": summary["fields"],
            "summary_path": summary_path.relative_to(case).as_posix(),
            "summary_sha256": _sha256(summary_path),
            "slices": slice_refs,
        }
        if occupied_qoi_ref is not None:
            manifest["occupied_qoi"] = occupied_qoi_ref
        _atomic_json(case / "result_manifest.json", manifest)
    except (OSError, ValueError, ET.ParseError) as exc:
        return {"ok": False, "error": str(exc), "case": str(case)}
    return {
        "ok": True, "case": str(case), "manifest": manifest,
        "manifest_path": str(case / "result_manifest.json"), "summary": summary,
    }


def compute_occupied_volume_qois_from_vtu(path, selector, floor_elevation_m=None):
    """Volume-weighted temperature/speed QoIs over a confirmed occupied-zone band.

    ``selector`` follows the ``occupied_volume_band.v1`` contract (an AGL
    z-band expressed in cell-center coordinates). ``floor_elevation_m`` must
    be supplied explicitly by the caller — there is no implicit floor guess —
    because it converts the raw VTU mesh coordinates (whatever datum the mesh
    was built in) into the AGL coordinate the selector was confirmed against.
    """
    if floor_elevation_m is None:
        raise PostprocessEvidenceError(
            "OCCUPIED_VTU_AGL_FLOOR_ELEVATION_REQUIRED: floor_elevation_m must be "
            "supplied explicitly to convert VTU mesh coordinates to AGL")
    floor_elevation_m = _require_finite_real(
        floor_elevation_m,
        "OCCUPIED_VTU_AGL_FLOOR_ELEVATION_INVALID: floor_elevation_m must be finite",
    )
    selector = _normalise_occupied_selector(selector)
    path = Path(path)
    data = read_internal_vtu(path)
    volumes = data.get("volume_m3")
    if volumes is None:
        raise PostprocessEvidenceError(
            "OCCUPIED_VTU_VOLUME_MISSING: VTU has no cell 'V' (volume) data array")
    if len(volumes) != data["cell_count"]:
        raise PostprocessEvidenceError(
            f"OCCUPIED_VTU_VOLUME_TUPLE_MISMATCH: expected {data['cell_count']} "
            f"volumes, found {len(volumes)}")
    volumes = [
        _require_finite_real(
            value,
            "OCCUPIED_VTU_VOLUME_INVALID: cell volumes must all be finite and positive",
            positive=True,
        )
        for value in volumes
    ]
    centres = []
    for centre in data["centres_m"]:
        if not isinstance(centre, (list, tuple)) or len(centre) != 3:
            raise PostprocessEvidenceError(
                "OCCUPIED_VTU_CENTRE_INVALID: cell centres must be finite xyz triples")
        centres.append(tuple(
            _require_finite_real(
                value,
                "OCCUPIED_VTU_CENTRE_INVALID: cell centres must be finite xyz triples",
            )
            for value in centre
        ))
    temperatures = [
        _require_finite_real(
            value,
            "OCCUPIED_VTU_TEMPERATURE_INVALID: cell temperatures must be finite",
        )
        for value in data["temperature_k"]
    ]
    velocity = []
    for vector in data["velocity_m_s"]:
        if not isinstance(vector, (list, tuple)) or len(vector) != 3:
            raise PostprocessEvidenceError(
                "OCCUPIED_VTU_VELOCITY_INVALID: cell velocities must be finite xyz triples")
        velocity.append(tuple(
            _require_finite_real(
                value,
                "OCCUPIED_VTU_VELOCITY_INVALID: cell velocities must be finite xyz triples",
            )
            for value in vector
        ))
    speeds = [math.sqrt(sum(value * value for value in vector)) for vector in velocity]

    selected = [
        index for index, centre in enumerate(centres)
        if sensitivity_job._cell_is_selected({
            "center_m": (centre[0], centre[1], centre[2] - floor_elevation_m),
        }, selector)
    ]
    selected_volume = sum(volumes[index] for index in selected)
    if not selected or not math.isfinite(selected_volume) or selected_volume <= 0:
        raise PostprocessEvidenceError(
            "OCCUPIED_VTU_SELECTION_EMPTY: no cells fall inside the confirmed "
            "occupied-zone band")

    mean_temperature = sum(
        temperatures[index] * volumes[index] for index in selected
    ) / selected_volume
    mean_speed = sum(
        speeds[index] * volumes[index] for index in selected
    ) / selected_volume
    p95_temperature = _weighted_percentile(
        [temperatures[index] for index in selected],
        [volumes[index] for index in selected],
        0.95,
    )
    p95_speed = _weighted_percentile(
        [speeds[index] for index in selected],
        [volumes[index] for index in selected],
        0.95,
    )
    if not math.isfinite(mean_temperature) or not math.isfinite(mean_speed):
        raise PostprocessEvidenceError(
            "OCCUPIED_VTU_QOI_INVALID: volume-weighted occupied-zone QOIs must be finite")

    return {
        "schema_version": 1,
        "contract": "occupied_volume_qoi.v1",
        "created_at": _now(),
        "source": str(path),
        "source_vtu_sha256": _sha256(path),
        "selector": selector,
        "selector_sha256": selector["selector_sha256"],
        "floor_elevation_m": floor_elevation_m,
        "coordinate_provenance": {
            "source_coordinate": "vtu_mesh_coordinates_m",
            "floor_elevation_m": floor_elevation_m,
            "output_coordinate": "cell_center_m_agl",
        },
        "selected_cell_count": len(selected),
        "selected_volume_m3": selected_volume,
        "scope": "selected_occupied_volume_band",
        "aggregation": "cell_volume_weighted_final_snapshot",
        "sample_count": len(selected),
        "temperature": {
            "mean_k": mean_temperature,
            "p95_k": p95_temperature,
        },
        "velocity": {
            "mean_speed_m_s": mean_speed,
            "p95_speed_m_s": p95_speed,
        },
        "occupied_zone_mean_temperature_k": mean_temperature,
        "occupied_zone_mean_speed_m_s": mean_speed,
    }


def _normalise_time_window_samples(timed_values, window_start_s, window_end_s,
                                   minimum_samples, code_prefix):
    start = _require_finite_real(
        window_start_s, f"{code_prefix}_WINDOW_INVALID: start must be finite")
    end = _require_finite_real(
        window_end_s, f"{code_prefix}_WINDOW_INVALID: end must be finite")
    if start >= end or not isinstance(minimum_samples, int) or isinstance(minimum_samples, bool):
        raise PostprocessEvidenceError(
            f"{code_prefix}_WINDOW_INVALID: window must have positive duration and "
            "minimum_samples must be an integer")
    if minimum_samples < 2:
        raise PostprocessEvidenceError(
            f"{code_prefix}_WINDOW_INVALID: minimum_samples must be at least two")
    ordered = sorted(timed_values, key=lambda item: item[0])
    if any(first[0] == second[0] for first, second in zip(ordered, ordered[1:])):
        raise PostprocessEvidenceError(
            f"{code_prefix}_DUPLICATE_TIME: sample times must be unique")
    in_window = [item for item in ordered if start <= item[0] <= end]
    if (not ordered or ordered[0][0] > start or ordered[-1][0] < end
            or len(in_window) < minimum_samples):
        raise PostprocessEvidenceError(
            f"{code_prefix}_WINDOW_NOT_COVERED: final window must be covered by at "
            f"least {minimum_samples} raw samples")
    return ordered, start, end


def _interpolate_at(ordered, target, value_index):
    for item in ordered:
        if item[0] == target:
            return item[value_index]
    for first, second in zip(ordered, ordered[1:]):
        if first[0] < target < second[0]:
            fraction = (target - first[0]) / (second[0] - first[0])
            return first[value_index] + fraction * (
                second[value_index] - first[value_index]
            )
    raise PostprocessEvidenceError(
        "TIME_WEIGHTED_QOI_WINDOW_NOT_COVERED: cannot interpolate window boundary")


def _trapezoidal_time_mean(ordered, start, end, value_index):
    points = [(start, _interpolate_at(ordered, start, value_index))]
    points.extend(
        (item[0], item[value_index])
        for item in ordered if start < item[0] < end
    )
    points.append((end, _interpolate_at(ordered, end, value_index)))
    integral = sum(
        (second[0] - first[0]) * (first[1] + second[1]) / 2.0
        for first, second in zip(points, points[1:])
    )
    mean = integral / (end - start)
    if not math.isfinite(mean):
        raise PostprocessEvidenceError(
            "TIME_WEIGHTED_QOI_VALUE_INVALID: integrated mean must be finite")
    return mean


def compute_time_weighted_occupied_volume_qois(
        paths, selector, floor_elevation_m, window_start_s, window_end_s,
        minimum_samples=5):
    """Integrate cell-volume-weighted occupied QoIs over a fixed final window."""
    if not isinstance(paths, (list, tuple)) or not paths:
        raise PostprocessEvidenceError(
            "TIME_WEIGHTED_QOI_SAMPLES_MISSING: VTU source list is empty")
    timed = []
    sources = []
    canonical_selector = None
    for raw_path in paths:
        path = Path(raw_path)
        parsed = read_internal_vtu(path)
        time_s = _require_finite_real(
            parsed.get("time_s"),
            "TIME_WEIGHTED_QOI_TIME_INVALID: every VTU must declare a finite TimeValue",
        )
        qoi = compute_occupied_volume_qois_from_vtu(
            path, selector, floor_elevation_m=floor_elevation_m)
        canonical_selector = qoi["selector"]
        timed.append((
            time_s,
            qoi["occupied_zone_mean_temperature_k"],
            qoi["occupied_zone_mean_speed_m_s"],
        ))
        sources.append({"time_s": time_s, "path": str(path), "sha256": _sha256(path)})
    ordered, start, end = _normalise_time_window_samples(
        timed, window_start_s, window_end_s, minimum_samples, "TIME_WEIGHTED_QOI")
    sources.sort(key=lambda item: item["time_s"])
    return {
        "schema_version": 1,
        "contract": "time_weighted_occupied_volume_qoi.v1",
        "created_at": _now(),
        "scope": "selected_occupied_volume_band",
        "aggregation": (
            "time_weighted_trapezoidal_of_cell_volume_weighted_snapshots.v1"
        ),
        "window": {"start_s": start, "end_s": end},
        "sample_count": sum(start <= item[0] <= end for item in ordered),
        "selector": canonical_selector,
        "selector_sha256": canonical_selector["selector_sha256"],
        "floor_elevation_m": float(floor_elevation_m),
        "sources": sources,
        "occupied_zone_mean_temperature_k": _trapezoidal_time_mean(
            ordered, start, end, 1),
        "occupied_zone_mean_speed_m_s": _trapezoidal_time_mean(
            ordered, start, end, 2),
    }


def _validated_source_refs(raw):
    if not isinstance(raw, list) or not raw:
        raise PostprocessEvidenceError(
            "EXHAUST_TIME_QOI_SOURCE_EVIDENCE_REQUIRED: T/phi source refs are required")
    normalised = []
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise PostprocessEvidenceError(
                "EXHAUST_TIME_QOI_SOURCE_EVIDENCE_REQUIRED: invalid source ref")
        path = item.get("path")
        digest = item.get("sha256")
        if (not isinstance(path, str) or not path.strip()
                or not isinstance(digest, str) or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)):
            raise PostprocessEvidenceError(
                "EXHAUST_TIME_QOI_SOURCE_EVIDENCE_REQUIRED: invalid source ref")
        normalised.append({"path": path.strip(), "sha256": digest})
    return normalised


def compute_time_weighted_exhaust_temperature_rise_qoi(
        samples, supply_temperature_k, window_start_s, window_end_s,
        minimum_samples=5):
    """Integrate solver-phi-derived, flow-weighted exhaust temperatures in time."""
    supply = _require_finite_real(
        supply_temperature_k,
        "EXHAUST_TIME_QOI_VALUE_INVALID: supply temperature must be finite")
    if not isinstance(samples, (list, tuple)) or not samples:
        raise PostprocessEvidenceError(
            "EXHAUST_TIME_QOI_SAMPLES_MISSING: exhaust samples are required")
    timed = []
    provenance = []
    for sample in samples:
        if not isinstance(sample, dict):
            raise PostprocessEvidenceError(
                "EXHAUST_TIME_QOI_VALUE_INVALID: each sample must be an object")
        time_s = _require_finite_real(
            sample.get("time_s"),
            "EXHAUST_TIME_QOI_VALUE_INVALID: sample time must be finite")
        exhausts = sample.get("exhausts")
        if not isinstance(exhausts, list) or not exhausts:
            raise PostprocessEvidenceError(
                "EXHAUST_TIME_QOI_VALUE_INVALID: exhaust list must be non-empty")
        weighted_sum = 0.0
        total_rate = 0.0
        for item in exhausts:
            if (not isinstance(item, dict)
                    or item.get("temperature_method") != TRUSTED_EXHAUST_TEMPERATURE_METHOD):
                raise PostprocessEvidenceError(
                    "EXHAUST_TIME_QOI_SOLVER_PROVENANCE_REQUIRED: every sample must "
                    "use positive solver phi and owner-cell temperature")
            rate = _require_finite_real(
                item.get("solved_outflow_rate_m3_s"),
                "EXHAUST_TIME_QOI_VALUE_INVALID: outflow must be finite and positive",
                positive=True)
            temperature = _require_finite_real(
                item.get("temperature_k"),
                "EXHAUST_TIME_QOI_VALUE_INVALID: exhaust temperature must be finite")
            total_rate += rate
            weighted_sum += rate * temperature
        refs = _validated_source_refs(sample.get("source_refs"))
        mean_temperature = weighted_sum / total_rate
        timed.append((time_s, mean_temperature, mean_temperature - supply))
        provenance.append({"time_s": time_s, "source_refs": refs})
    ordered, start, end = _normalise_time_window_samples(
        timed, window_start_s, window_end_s, minimum_samples, "EXHAUST_TIME_QOI")
    provenance.sort(key=lambda item: item["time_s"])
    return {
        "schema_version": 1,
        "contract": "time_weighted_exhaust_temperature_rise_qoi.v1",
        "created_at": _now(),
        "aggregation": "time_weighted_trapezoidal_of_positive_phi_weighted_samples.v1",
        "window": {"start_s": start, "end_s": end},
        "sample_count": sum(start <= item[0] <= end for item in ordered),
        "supply_temperature_k": supply,
        "flow_weighted_exhaust_temperature_k": _trapezoidal_time_mean(
            ordered, start, end, 1),
        "exhaust_temperature_rise_k": _trapezoidal_time_mean(
            ordered, start, end, 2),
        "samples": provenance,
        "provenance": {
            "energy_closure_basis": TRUSTED_EXHAUST_ENERGY_CLOSURE_BASIS,
            "temperature_method": TRUSTED_EXHAUST_TEMPERATURE_METHOD,
        },
    }


def read_time_weighted_exhaust_temperature_rise_from_case(
        case_dir, window_start_s, window_end_s, minimum_samples=5):
    """Recompute final-window exhaust rise from saved OpenFOAM ``T``/``phi``."""
    import cfd_physics

    case = Path(case_dir).expanduser().resolve(strict=True)
    try:
        thermal_input = json.loads(
            (case / "thermal_input.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PostprocessEvidenceError(
            "EXHAUST_TIME_QOI_THERMAL_INPUT_INVALID: thermal_input.json is required"
        ) from error
    if not isinstance(thermal_input, dict):
        raise PostprocessEvidenceError("EXHAUST_TIME_QOI_THERMAL_INPUT_INVALID")
    settings = thermal_input.get("settings")
    terminals = thermal_input.get("terminals")
    if not isinstance(settings, dict) or not isinstance(terminals, list):
        raise PostprocessEvidenceError("EXHAUST_TIME_QOI_THERMAL_INPUT_INVALID")
    supply = _require_finite_real(
        settings.get("supply_temperature_k"),
        "EXHAUST_TIME_QOI_THERMAL_INPUT_INVALID: supply temperature is required")
    exhaust_patches = [
        item.get("mesh_patch_name") for item in terminals
        if isinstance(item, dict) and item.get("role") == "exhaust"
        and isinstance(item.get("mesh_patch_name"), str)
        and item.get("mesh_patch_name").strip()
    ]
    if not exhaust_patches:
        raise PostprocessEvidenceError(
            "EXHAUST_TIME_QOI_THERMAL_INPUT_INVALID: exhaust terminals are required")

    timed_directories = []
    for child in case.iterdir():
        if not child.is_dir() or child.is_symlink():
            continue
        try:
            time_s = float(child.name)
        except ValueError:
            continue
        if math.isfinite(time_s) and window_start_s <= time_s <= window_end_s:
            timed_directories.append((time_s, child))
    timed_directories.sort(key=lambda item: item[0])
    samples = []
    for time_s, time_dir in timed_directories:
        temperature_path = time_dir / "T"
        phi_path = time_dir / "phi"
        if (temperature_path.is_symlink() or phi_path.is_symlink()
                or not temperature_path.is_file() or not phi_path.is_file()):
            raise PostprocessEvidenceError(
                "EXHAUST_TIME_QOI_SOURCE_EVIDENCE_REQUIRED: saved T and phi are required")
        try:
            internal_t = cfd_physics._internal_scalar_values(temperature_path)
        except OSError as error:
            raise PostprocessEvidenceError(
                "EXHAUST_TIME_QOI_SOURCE_EVIDENCE_REQUIRED: T field is unreadable"
            ) from error
        if not internal_t:
            raise PostprocessEvidenceError(
                "EXHAUST_TIME_QOI_SOURCE_EVIDENCE_REQUIRED: T internalField is invalid")
        exhausts = []
        for patch_name in exhaust_patches:
            solved = cfd_physics._exhaust_flux_temperature(
                case, time_dir, patch_name, internal_t)
            if (not isinstance(solved, dict)
                    or solved.get("method") != TRUSTED_EXHAUST_TEMPERATURE_METHOD):
                raise PostprocessEvidenceError(
                    "EXHAUST_TIME_QOI_SOLVER_PROVENANCE_REQUIRED: positive phi "
                    f"evidence is missing for {patch_name}")
            exhausts.append({
                "mesh_patch_name": patch_name,
                "temperature_k": solved.get("temperature_k"),
                "solved_outflow_rate_m3_s": solved.get("flow_rate_m3_s"),
                "temperature_method": solved.get("method"),
            })
        samples.append({
            "time_s": time_s,
            "source_refs": [
                {
                    "path": temperature_path.relative_to(case).as_posix(),
                    "sha256": _sha256(temperature_path),
                },
                {
                    "path": phi_path.relative_to(case).as_posix(),
                    "sha256": _sha256(phi_path),
                },
            ],
            "exhausts": exhausts,
        })
    return compute_time_weighted_exhaust_temperature_rise_qoi(
        samples,
        supply_temperature_k=supply,
        window_start_s=window_start_s,
        window_end_s=window_end_s,
        minimum_samples=minimum_samples,
    )


def read_trusted_exhaust_temperature_rise_qoi(path, expected_run_manifest_sha256=None):
    """Read exhaust temperature rise from a run manifest, but only if it is
    both hash-pinned by the caller and backed by solver phi/temperature
    evidence rather than a design-flow fallback.

    The caller must pass the manifest hash it already trusts (e.g. from a
    result gate it just evaluated) — this function will not silently trust
    whatever JSON currently sits on disk.
    """
    path = Path(path)
    if expected_run_manifest_sha256 is None:
        raise PostprocessEvidenceError(
            "EXHAUST_QOI_TRUSTED_MANIFEST_HASH_REQUIRED: caller must supply the "
            "run_manifest sha256 it trusts")
    actual_hash = _sha256(path)
    if actual_hash != expected_run_manifest_sha256:
        raise PostprocessEvidenceError(
            "EXHAUST_QOI_TRUSTED_MANIFEST_HASH_MISMATCH: run_manifest on disk does "
            "not match the hash the caller trusts")

    try:
        run = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PostprocessEvidenceError(
            "EXHAUST_QOI_VALUE_INVALID: run_manifest must be readable JSON evidence"
        ) from error
    if not isinstance(run, dict):
        raise PostprocessEvidenceError(
            "EXHAUST_QOI_VALUE_INVALID: run_manifest must be a JSON object")
    thermal = run.get("thermal")
    if thermal is None:
        thermal = {}
    if not isinstance(thermal, dict):
        raise PostprocessEvidenceError(
            "EXHAUST_QOI_VALUE_INVALID: thermal evidence must be a JSON object")
    exhausts = thermal.get("exhausts") or []
    basis = thermal.get("energy_closure_basis")
    if basis != TRUSTED_EXHAUST_ENERGY_CLOSURE_BASIS or not exhausts:
        raise PostprocessEvidenceError(
            "EXHAUST_QOI_SOLVER_PROVENANCE_REQUIRED: energy_closure_basis is not "
            "solver-derived phi/temperature evidence")
    if not isinstance(exhausts, list):
        raise PostprocessEvidenceError(
            "EXHAUST_QOI_VALUE_INVALID: exhausts must be a non-empty JSON list")
    validated_exhausts = []
    for item in exhausts:
        if not isinstance(item, dict):
            raise PostprocessEvidenceError(
                "EXHAUST_QOI_VALUE_INVALID: each exhaust evidence item must be an object")
        rate = item.get("solved_outflow_rate_m3_s")
        method = item.get("temperature_method")
        if method != TRUSTED_EXHAUST_TEMPERATURE_METHOD or rate is None:
            raise PostprocessEvidenceError(
                "EXHAUST_QOI_SOLVER_PROVENANCE_REQUIRED: exhaust "
                f"{item.get('mesh_patch_name')!r} is not backed by positive solver phi")
        rate = _require_finite_real(
            rate,
            "EXHAUST_QOI_VALUE_INVALID: solved exhaust outflow rates must be finite",
            positive=True,
        )
        temperature = _require_finite_real(
            item.get("temperature_k"),
            "EXHAUST_QOI_VALUE_INVALID: solved exhaust temperatures must be finite",
        )
        validated_exhausts.append((rate, temperature))

    total_rate = sum(rate for rate, _ in validated_exhausts)
    if not math.isfinite(total_rate) or total_rate <= 0:
        raise PostprocessEvidenceError(
            "EXHAUST_QOI_VALUE_INVALID: total solved exhaust outflow must be finite and positive")
    flow_weighted_temperature = sum(
        temperature * rate for rate, temperature in validated_exhausts
    ) / total_rate
    settings = run.get("effective_settings")
    if settings is None:
        settings = {}
    if not isinstance(settings, dict):
        raise PostprocessEvidenceError(
            "EXHAUST_QOI_VALUE_INVALID: effective_settings must be a JSON object")
    supply_temperature = settings.get("supply_temperature_k")
    if supply_temperature is None:
        raise PostprocessEvidenceError(
            "EXHAUST_QOI_SUPPLY_TEMPERATURE_MISSING: run_manifest has no "
            "effective_settings.supply_temperature_k")
    supply_temperature = _require_finite_real(
        supply_temperature,
        "EXHAUST_QOI_VALUE_INVALID: supply temperature must be finite",
    )
    if not math.isfinite(flow_weighted_temperature):
        raise PostprocessEvidenceError(
            "EXHAUST_QOI_VALUE_INVALID: flow-weighted exhaust temperature must be finite")
    temperature_rise = flow_weighted_temperature - supply_temperature
    if not math.isfinite(temperature_rise):
        raise PostprocessEvidenceError(
            "EXHAUST_QOI_VALUE_INVALID: exhaust temperature rise must be finite")

    return {
        "schema_version": 1,
        "contract": "exhaust_temperature_rise_qoi.v1",
        "created_at": _now(),
        "source": str(path),
        "run_manifest_sha256": actual_hash,
        "supply_temperature_k": supply_temperature,
        "flow_weighted_exhaust_temperature_k": flow_weighted_temperature,
        "exhaust_temperature_rise_k": temperature_rise,
        "total_outflow_rate_m3_s": total_rate,
        "exhaust_count": len(exhausts),
        "provenance": {
            "energy_closure_basis": basis,
            "temperature_method": TRUSTED_EXHAUST_TEMPERATURE_METHOD,
        },
    }
