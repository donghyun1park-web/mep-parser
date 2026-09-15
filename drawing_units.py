"""One DXF-to-mm policy for inventory, model and source overlay; no size guessing."""
import math

from ezdxf import units


def positive_scale(value):
    if isinstance(value, bool):
        raise ValueError('unit_scale_to_mm must be a finite positive number')
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError('unit_scale_to_mm must be a finite positive number') from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError('unit_scale_to_mm must be a finite positive number')
    return value


def scale_to_mm(doc, explicit=None):
    if explicit is not None:
        return positive_scale(explicit)
    code = int(doc.header.get('$INSUNITS', 0))
    if code == 0:
        return None
    try:
        return positive_scale(units.conversion_factor(code, units.MM))
    except (ValueError, TypeError, ZeroDivisionError, IndexError):
        return None


def option_scale(options):
    """Read old MEP profiles and unit-only architectural settings without divergence."""
    direct = options.get('unit_scale_to_mm')
    profile = (options.get('mep_profile') or {}).get('unit_scale_to_mm')
    if direct is not None and profile is not None:
        if not math.isclose(positive_scale(direct), positive_scale(profile), rel_tol=1e-12):
            raise ValueError('Conflicting source and MEP unit scales; review source units')
    return profile if profile is not None else direct


def uses_legacy_units(source):
    # Old durable projects used the parser's meter/otherwise-mm policy. Raw
    # standalone source descriptors and newly created projects use the header.
    return ('fingerprints' in source and source.get('unit_policy') != 'header'
            and (source.get('options') or {}).get('mep_profile') is None)


def unit_review(doc, explicit=None, legacy=False, legacy_header=False):
    header = scale_to_mm(doc)
    scale = scale_to_mm(doc, explicit)
    basis = 'explicit' if explicit is not None else 'header' if header is not None else 'unresolved'
    if scale is None and legacy:
        scale, basis = 1., 'legacy_assumed_mm'
    code = int(doc.header.get('$INSUNITS', 0))
    if explicit is None and legacy_header:
        scale = 1000. if code == 6 else 1.
        basis = 'legacy_header_policy'
    names = {0: '미지정', 1: 'inch', 2: 'ft', 3: 'mile', 4: 'mm', 5: 'cm', 6: 'm', 7: 'km'}
    differs = header is not None and scale is not None and not math.isclose(header, scale, rel_tol=1e-9)
    warnings = []
    if header is None:
        warnings.append('DXF units unknown; explicit unit_scale_to_mm required for confirmed units')
    if differs:
        warnings.append(f'DXF header unit scale {header:g} differs from applied scale {scale:g}; basis={basis}')
    return {'insunits': code, 'header_unit': names.get(code, f'INSUNITS {code}'),
            'header_scale_to_mm': header, 'effective_scale_to_mm': scale,
            'basis': basis, 'header_differs': differs, 'warnings': warnings}
