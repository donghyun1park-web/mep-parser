"""
cfd_validate.py — 벤치마크 검증: IEA Annex 20 등온 슬롯취출 방 (2D1)

파이프라인(cfd_export→cfd_run)이 만든 Annex 20 케이스의 풍속 프로파일을 문헌 실측
(cfd_benchmarks/annex20/measured_x2H.csv, 디지타이즈·오차 명시)과 오버레이해
"이 도구의 오차는 이 정도"의 공식 근거를 만든다. (계획 D3, REHVA GB10 방법론)

사용:
  python cfd_export.py cfd_benchmarks/annex20/annex20.json -o case_annex20
  python cfd_run.py case_annex20
  python cfd_validate.py case_annex20            # → annex20_validation.png + 판정

검증 항목:
  1) 정량: x/H=2.0 중앙면 수직 u/U0 프로파일 vs 실측(RMS·최대편차)
  2) 정성: 천장 제트 부착(Coanda) · 바닥 역류 존재 · 제트 감쇠(x=H→2H)
"""
import argparse
import csv
import os
import sys

import cfd_report

HERE = os.path.dirname(os.path.abspath(__file__))
MEASURED = os.path.join(HERE, "cfd_benchmarks", "annex20", "measured_x2H.csv")


def load_measured(path=MEASURED):
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.reader(f):
            if not r or r[0].startswith("#") or r[0] == "y_over_H":
                continue
            rows.append((float(r[0]), float(r[1])))
    return rows


def profile_at(case_dir, x_over_H):
    """케이스 결과에서 x=x_over_H·H, y=중앙 수직선의 u(=Ux)/U0 프로파일.
    반환 (y_over_H 리스트[천장=0], u_over_U0 리스트, U0)."""
    import numpy as np
    meta = cfd_report._load_meta(case_dir)
    n = meta["mesh"]["cells"]
    nx, ny, nz = meta["mesh"]["nx"], meta["mesh"]["ny"], meta["mesh"]["nz"]
    room = meta["config"]["room"]
    L, H = room["L"], room["H"]
    tdir = cfd_report.find_latest_time(case_dir)
    if not tdir:
        raise SystemExit("결과 time 디렉토리 없음 — 먼저 cfd_run.py 실행")
    U = cfd_report._as_array(cfd_report.read_field(os.path.join(tdir, "U")), n)
    Ug = U[:nx * ny * nz].reshape(nz, ny, nx, 3)
    # U0 = 급기 슬롯 실풍량/실면적 (meta.patches — 스냅 반영 정확값)
    sups = [p for p in meta.get("patches", []) if p["role"] == "supply"]
    U0 = sum(p["cmh"] for p in sups) / 3600.0 / sum(p["area"] for p in sups)
    i = min(nx - 1, int(round(x_over_H * H / L * nx - 0.5)))
    j = ny // 2
    ux = Ug[:, j, i, 0]                      # (nz,) 바닥→천장
    zc = (np.arange(nz) + 0.5) * H / nz
    y_over_H = 1.0 - zc / H                  # 천장=0 (논문 좌표)
    return list(y_over_H)[::-1], list(ux / U0)[::-1], U0


def validate(case_dir, out_png=None):
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    meas = load_measured()
    yH2, uU2, U0 = profile_at(case_dir, 2.0)
    yH1, uU1, _ = profile_at(case_dir, 1.0)

    # 정량: 실측 y 위치에서 CFD 보간 → 편차
    cfd_interp = np.interp([m[0] for m in meas], yH2, uU2)
    diff = cfd_interp - np.array([m[1] for m in meas])
    rms = float(np.sqrt((diff ** 2).mean()))
    mx = float(np.abs(diff).max())

    # 정성 3항목
    q_jet = max(uU2[:4]) > 0.3                    # 천장 부근(상위 4셀) 제트 존재
    q_return = min(uU2[-6:]) < -0.1               # 바닥 부근 역류
    q_decay = max(uU1) > max(uU2)                 # 제트 감쇠(x=H → 2H)

    fig, ax = plt.subplots(figsize=(6.4, 7))
    ax.plot(uU2, yH2, "-", color="#2c5f8a", lw=2, label="CFD (this pipeline), x/H=2")
    ax.plot(uU1, yH1, "--", color="#888", lw=1.2, label="CFD, x/H=1")
    ax.plot([m[1] for m in meas], [m[0] for m in meas], "o", color="#c0392b", ms=5,
            label="Benchmark LDA (digitized +/-0.03)")
    ax.set_xlabel("u / U0")
    ax.set_ylabel("y / H  (0 = ceiling)")
    ax.invert_yaxis()
    ax.grid(alpha=0.3)
    ax.set_title(f"IEA Annex 20 (2D1)  x/H=2.0 profile   RMS={rms:.03f}, max={mx:.03f}")
    ax.legend(fontsize=9, loc="lower right")
    fig.tight_layout()
    out_png = out_png or os.path.join(case_dir, "annex20_validation.png")
    fig.savefig(out_png, dpi=130)
    plt.close(fig)

    print(f"U0(실효) = {U0:.4f} m/s")
    print(f"[정량] x/H=2 프로파일 vs 실측(디지타이즈): RMS {rms:.3f}·U0, 최대 {mx:.3f}·U0"
          f"  (디지타이즈 오차 ±0.03 포함)")
    print(f"[정성] 천장 제트 부착: {'✓' if q_jet else '✗'}"
          f" · 바닥 역류: {'✓' if q_return else '✗'}"
          f" · 제트 감쇠(x=H→2H): {'✓' if q_decay else '✗'}")
    verdict = "통과" if (rms <= 0.10 and q_jet and q_return and q_decay) else \
              ("부분통과(정성 OK, 정량 편차 큼)" if (q_jet and q_return and q_decay) else "불통과")
    print(f"판정: {verdict}  (기준: RMS ≤ 0.10·U0 + 정성 3항목)")
    print(f"오버레이 → {out_png}")
    return {"rms": rms, "max": mx, "qual": [q_jet, q_return, q_decay], "verdict": verdict}


def main():
    ap = argparse.ArgumentParser(description="Annex 20 벤치마크 검증(프로파일 오버레이)")
    ap.add_argument("case", help="annex20 케이스 디렉토리(실행 완료본)")
    ap.add_argument("-o", "--out", default=None, help="PNG 경로")
    args = ap.parse_args()
    validate(args.case, args.out)


if __name__ == "__main__":
    main()
