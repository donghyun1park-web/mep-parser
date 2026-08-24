"""Run or resume the project-local automatic mesh-uncertainty acceptance job."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cfd_gci_job
import release_audit


def _emit(payload):
    """Write one machine-readable progress event immediately."""
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def _refresh_release_readiness(root):
    """Regenerate release evidence after a GCI attempt without masking its result."""
    audit = release_audit.build_release_audit(root)
    report = Path(audit["manifest_path"]).with_suffix(".html")
    release_audit.generate_html(audit["manifest"], report)
    return {
        **audit,
        "report_path": str(report.resolve()),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Create and run (or resume) the automatic mesh-uncertainty study."
    )
    parser.add_argument(
        "--geometry",
        default="cfd_benchmarks/g2_thermal/geometry.json",
        help="geometry.v2 JSON input (default: the retained G2 benchmark)",
    )
    parser.add_argument(
        "--root",
        default="cfd_projects",
        help="Project output root (default: cfd_projects)",
    )
    parser.add_argument(
        "--study",
        help="resume an existing deterministic study id instead of creating one",
    )
    parser.add_argument(
        "--widths",
        nargs="+",
        type=float,
        metavar="WIDTH_M",
        help="optional 4 background widths for v3, ordered coarse to fine",
    )
    parser.add_argument(
        "--contract", default="grid_convergence.v3",
        choices=("grid_convergence.v1", "grid_convergence.v2", "grid_convergence.v3"),
        help="uncertainty contract (default: grid_convergence.v3)",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    if args.study:
        manifest = cfd_gci_job.load_study(root, args.study)
        created = (
            {"ok": True, "study": args.study, "manifest": manifest, "resumed": True}
            if manifest is not None
            else {"ok": False, "error": f"study not found: {args.study}"}
        )
    else:
        geometry = Path(args.geometry).expanduser().resolve()
        settings = {"gci_contract": args.contract}
        if args.widths:
            settings["mesh_widths_m"] = args.widths
        created = cfd_gci_job.create_study(root, geometry, settings)
    _emit({
        "event": "study_ready" if created.get("ok") else "study_error",
        "result": created,
    })
    if not created.get("ok"):
        return 2

    result = cfd_gci_job.run_study(root, created["study"], callback=_emit)
    _emit({
        "event": "study_complete" if result.get("ok") else "study_failed",
        "result": result,
    })
    try:
        audit = _refresh_release_readiness(root)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        _emit({
            "event": "release_readiness_failed",
            "result": {"ok": False, "error": str(exc)},
        })
    else:
        _emit({"event": "release_readiness_updated", "result": audit})
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
