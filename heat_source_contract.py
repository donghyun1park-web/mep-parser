"""Canonical, traceable heat-source input contract.

This module deliberately has no OpenFOAM or geometry dependency.  It turns a
user-confirmed equipment load into one numeric contract that both the legacy
porous-zone exporter and the body-fitted exporter can consume without changing
the input power or heat split.
"""
from collections.abc import Mapping
import math


FRACTION_TOLERANCE = 1e-6
ALLOWED_HEAT_SOURCE_TYPES = frozenset({
    "user_confirmed",
    "legacy_manual_input",
    "fixture",
})


class HeatSourceContractError(ValueError):
    """Raised when a heat source cannot be used as a traceable CFD input."""


def _finite_number(value, field):
    if isinstance(value, bool):
        raise HeatSourceContractError(f"{field} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise HeatSourceContractError(f"{field} must be a finite number") from exc
    if not math.isfinite(number):
        raise HeatSourceContractError(f"{field} must be a finite number")
    return number


def _nonempty_text(value):
    if value is None:
        return ""
    return str(value).strip()


def _power_candidates(source):
    candidates = []
    for field, multiplier in (("input_power_w", 1.0),
                              ("power_kw", 1000.0),
                              ("kw", 1000.0)):
        if field not in source or source[field] is None:
            continue
        power_w = _finite_number(source[field], field) * multiplier
        candidates.append((field, power_w))
    if not candidates:
        raise HeatSourceContractError(
            "input power is required as input_power_w, power_kw, or kw"
        )
    first_field, first_power_w = candidates[0]
    for field, power_w in candidates[1:]:
        if not math.isclose(power_w, first_power_w, rel_tol=1e-9, abs_tol=1e-6):
            raise HeatSourceContractError(
                "conflicting input power fields: "
                f"{first_field} and {field} do not represent the same power"
            )
    if first_power_w <= 0:
        raise HeatSourceContractError("input power must be greater than zero")
    return first_field, first_power_w


def _fraction(value, field):
    fraction = _finite_number(value, field)
    if not 0.0 <= fraction <= 1.0:
        raise HeatSourceContractError(f"{field} must be between 0 and 1")
    return fraction


def _identity_text(value):
    """Return a usable CAD identity token without accepting booleans."""
    # DXF handles are scalar tokens.  ``str(dict)`` used to turn a forged JSON
    # object into a seemingly non-empty handle; reject every structured value
    # before converting it to text.
    if value is None or isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    return _nonempty_text(value)


def _has_dxf_identity(source_ref):
    """Whether a reference retains a handle-level CAD identity."""
    if not isinstance(source_ref, Mapping):
        return False
    for key in ("handle", "source_handle"):
        if _identity_text(source_ref.get(key)):
            return True
    handles = source_ref.get("handles")
    if isinstance(handles, (list, tuple, set, frozenset)):
        return any(_identity_text(value) for value in handles)
    return False


def _is_explicit_manual_input_ref(source_ref, source_id):
    """Recognize the bounded provenance shape generated for manual inputs."""
    if not isinstance(source_ref, Mapping):
        return False
    entity_type = _identity_text(source_ref.get("entity_type")).upper()
    layer = _identity_text(source_ref.get("layer")).upper()
    referenced_id = _identity_text(source_ref.get("source_id"))
    return (
        entity_type in {"UI_INPUT", "LEGACY_UI_INPUT"}
        and layer == "USER_CONFIRMED"
        and bool(referenced_id)
        and referenced_id.casefold() == source_id.casefold()
    )


def source_reference_kind(source_ref, source_id, *, override_of_dxf=False):
    """Classify one confirmed source reference or fail closed.

    The contract distinguishes a real DXF handle from an explicit user-input
    record.  It deliberately refuses arbitrary annotation dictionaries: those
    cannot be reconciled with either a CAD object or a reviewed manual value.
    A DXF-derived override must retain a handle identity even after the user
    changes its CFD values.
    """
    source_id = _nonempty_text(source_id)
    if not isinstance(source_ref, Mapping) or not source_ref:
        raise HeatSourceContractError(
            "source_ref is required for a confirmed heat source"
        )
    if _has_dxf_identity(source_ref):
        return "dxf"
    if override_of_dxf:
        raise HeatSourceContractError(
            "source_ref for a DXF override must retain a CAD handle"
        )
    if _is_explicit_manual_input_ref(source_ref, source_id):
        return "manual_input"
    raise HeatSourceContractError(
        "source_ref must contain a CAD handle or explicit USER_CONFIRMED manual provenance"
    )


def _confirmed_source_ref(source, *, override_of_dxf=False):
    """Return a bounded source reference plus its durable provenance kind."""
    source_ref = source.get("source_ref")
    kind = source_reference_kind(
        source_ref,
        source.get("source_id"),
        override_of_dxf=override_of_dxf,
    )
    return source_ref, kind


def normalize_confirmed_heat_source(source, *, fraction_tolerance=FRACTION_TOLERANCE):
    """Return a canonical heat-source record suitable for every CFD adapter.

    Accepted load units are ``input_power_w``, ``power_kw``, or legacy ``kw``.
    The convective fraction is required.  Radiation is derived as the remaining
    fraction only when no ``radiative_fraction`` is supplied; an explicit split
    is retained after validating its closure.  ``user_confirmed`` sources must
    carry an equipment identity and evidence so an automatic DXF detection can
    never become a citable heat load by accident.
    """
    if not isinstance(source, Mapping):
        raise HeatSourceContractError("heat source must be an object")
    tolerance = _finite_number(fraction_tolerance, "fraction_tolerance")
    if tolerance < 0:
        raise HeatSourceContractError("fraction_tolerance must be non-negative")

    source_type_input = _nonempty_text(source.get("source_type"))
    if not source_type_input:
        raise HeatSourceContractError("source_type is required")
    source_type = source_type_input.casefold()
    if source_type not in ALLOWED_HEAT_SOURCE_TYPES:
        accepted = ", ".join(sorted(ALLOWED_HEAT_SOURCE_TYPES))
        raise HeatSourceContractError(
            f"source_type must be one of: {accepted}; "
            "DXF detections must be reviewed as user_confirmed first"
        )
    source_id = _nonempty_text(source.get("source_id"))
    evidence = _nonempty_text(source.get("evidence"))
    confirmed = source_type == "user_confirmed"
    if confirmed and not source_id:
        raise HeatSourceContractError(
            "source_id is required for a confirmed heat source"
        )
    if confirmed and not evidence:
        raise HeatSourceContractError(
            "evidence is required for a confirmed heat source"
        )
    override_of_dxf = source.get("override_of_dxf")
    override_of_dxf_present = override_of_dxf is not None
    if override_of_dxf_present and not isinstance(override_of_dxf, bool):
        raise HeatSourceContractError("override_of_dxf must be a boolean")
    source_ref_kind = None
    if confirmed:
        source_ref, source_ref_kind = _confirmed_source_ref(
            source, override_of_dxf=override_of_dxf is True
        )
    else:
        source_ref = source.get("source_ref")

    source_label = _nonempty_text(source.get("source_label"))
    if not source_label:
        source_label = _nonempty_text(source.get("name")) or source_id
    if not source_label:
        raise HeatSourceContractError("source_label or name is required")

    power_input, input_power_w = _power_candidates(source)
    if "convective_fraction" not in source or source["convective_fraction"] is None:
        raise HeatSourceContractError("convective_fraction is required")
    convective_fraction = _fraction(
        source["convective_fraction"], "convective_fraction"
    )

    explicit_radiative = (
        "radiative_fraction" in source and source["radiative_fraction"] is not None
    )
    if explicit_radiative:
        explicit_radiative_fraction = _fraction(
            source["radiative_fraction"], "radiative_fraction"
        )
        if abs(convective_fraction + explicit_radiative_fraction - 1.0) > tolerance:
            raise HeatSourceContractError(
                "convective_fraction and radiative_fraction must sum to 1"
            )
        # Even when a user input is within tolerance, make the persisted
        # canonical fraction exactly match the W-level remainder below.
        radiative_fraction = round(1.0 - convective_fraction, 12)
    else:
        # Fractions are persisted in JSON contracts and compared across
        # adapters.  Normalize the harmless binary subtraction tail (for
        # example 1 - 0.8) without changing W-level energy closure below.
        radiative_fraction = round(1.0 - convective_fraction, 12)

    convective_power_w = input_power_w * convective_fraction
    # Keep the W-level closure exact even when a decimal fraction (for example
    # 0.8) cannot be represented exactly in binary floating point.
    radiative_power_w = input_power_w - convective_power_w
    provenance = {
        "source_id": source_id,
        "source_label": source_label,
        "evidence": evidence,
        "source_type": source_type,
        "power_input": power_input,
        "radiative_fraction": "explicit" if explicit_radiative else "derived",
    }
    if source_ref not in (None, ""):
        provenance["source_ref"] = source_ref
    if source_ref_kind is not None:
        provenance["source_reference_kind"] = source_ref_kind
    if override_of_dxf_present:
        provenance["override_of_dxf"] = override_of_dxf

    canonical = {
        "contract": "heat_source.v1",
        "source_id": source_id,
        "source_label": source_label,
        # Existing consumers call this legacy field ``name``.  Keep it in
        # addition to source_label so adapters can migrate independently.
        "name": source_label,
        "source_type": source_type,
        "evidence": evidence,
        "input_power_w": input_power_w,
        "power_kw": input_power_w / 1000.0,
        "convective_fraction": convective_fraction,
        "radiative_fraction": radiative_fraction,
        "convective_power_w": convective_power_w,
        "radiative_power_w": radiative_power_w,
        "excluded_radiative_power_w": radiative_power_w,
        "provenance": provenance,
    }
    if "source_ref" in provenance:
        canonical["source_ref"] = provenance["source_ref"]
    if override_of_dxf_present:
        canonical["override_of_dxf"] = override_of_dxf
    return canonical


def assert_unique_positive_source_ids(sources):
    """Validate canonical positive heat sources and return their source IDs.

    A caller that combines heat-source adapters must use this before applying
    the loads.  It stops one DXF item from appearing in more than one adapter
    and rejects incomplete or non-positive records instead of silently
    skipping them.
    """
    if isinstance(sources, (str, bytes, Mapping)):
        raise HeatSourceContractError("canonical heat sources must be an iterable")
    try:
        iterator = iter(sources)
    except TypeError as exc:
        raise HeatSourceContractError("canonical heat sources must be an iterable") from exc

    source_ids = []
    seen_ids = {}
    for index, source in enumerate(iterator):
        if not isinstance(source, Mapping):
            raise HeatSourceContractError(
                f"canonical heat source at index {index} must be an object"
            )
        if source.get("contract") != "heat_source.v1":
            raise HeatSourceContractError(
                f"canonical heat source at index {index} has an invalid contract"
            )
        source_id = _nonempty_text(source.get("source_id"))
        if not source_id:
            raise HeatSourceContractError(
                f"positive heat source at index {index} requires source_id"
            )
        input_power_w = _finite_number(
            source.get("input_power_w"),
            f"canonical heat source {source_id} input_power_w",
        )
        if input_power_w <= 0:
            raise HeatSourceContractError(
                f"canonical heat source {source_id} must have positive input_power_w"
            )
        identity_key = source_id.casefold()
        if identity_key in seen_ids:
            raise HeatSourceContractError(
                f"duplicate source_id for positive heat source: {source_id}"
            )
        seen_ids[identity_key] = index
        source_ids.append(source_id)
    return tuple(source_ids)


# Shorter name for future adapters; the explicit name remains the public API.
normalize_heat_source = normalize_confirmed_heat_source
