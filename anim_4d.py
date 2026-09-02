# -*- coding: utf-8 -*-
"""geometry.json → 4D 시공순서 애니메이션 (아이소메트릭 렌더 + mp4)

FreeCAD/외부 렌더러 없이 geometry.json 만으로 시공 단계별 프레임을 생성한다.
- 각 요소를 프리즘(밑면 폴리곤 + z범위)으로 변환
- 아이소메트릭 투영 + 화가 알고리즘(깊이 정렬)으로 PIL 렌더
- 단계 누적 → 회전하며 ffmpeg 로 mp4 인코딩

사용: python anim_4d.py <geometry.json> [-o out.mp4] [--fps 24] [--width 1600]
"""
import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont

# ── 시공 단계 정의 (순서 = 실제 시공 순서) ──────────────────────────────────
# ⚠ 아래 z 값과 레이어명은 특정 현장 기준 샘플이다. 다른 현장에는 그대로 못 쓴다.
# TODO: stack.json(선언적 층 조립)이 생기면 levels 선언에서 읽어 프로젝트 독립으로 만들 것.
STAGES = [
    ("1. 매트기초",        lambda c, r: c == "column" and abs(r.get("z_base", 0) - 1200) < 1),
    ("2. PIT 바닥슬래브",   lambda c, r: c == "slab" and r.get("layer") == "generated_from_exterior_walls_bbox"),
    ("3. PIT 벽·기둥",     lambda c, r: c in ("wall", "column") and abs(r.get("z_base", 0) - 2500) < 1),
    ("4. 1층 바닥 보",     lambda c, r: c == "slab" and r.get("layer") == "00-보_generated_downstand"),
    ("5. 1층 슬래브",      lambda c, r: c == "slab" and r.get("layer") == "generated_1F_slab_bbox"),
    ("6. 1층 벽·기둥",     lambda c, r: c in ("wall", "column") and abs(r.get("z_base", 0) - 6050) < 1),
    ("7. 중층 거더",       lambda c, r: c == "slab" and r.get("layer", "").endswith("GIRDER_generated")),
    ("8. 중층 바닥판",     lambda c, r: c == "slab" and r.get("layer") == "중층_deck_generated"),
    ("9. 중층 벽",        lambda c, r: c == "wall" and abs(r.get("z_base", 0) - 10550) < 1),
    ("10. 옥상 철골",      lambda c, r: c == "slab" and r.get("layer", "").startswith("ROOF_")),
    ("11. 옥상 지붕판",    lambda c, r: c == "slab" and r.get("layer") == "옥상_deck_generated"),
    ("12. 펜트하우스 기둥·벽", lambda c, r: c in ("wall", "column") and abs(r.get("z_base", 0) - 14750) < 1),
    ("13. 펜트하우스 골조·지붕", lambda c, r: c == "slab" and r.get("layer", "").startswith("PH_")),
]

# 단계별 색 (구조해석 모델 관례: RC=적, 철골=청, 코어=황)
STAGE_COLOR = [
    (150, 150, 155), (170, 170, 175), (196, 80, 72), (196, 80, 72), (205, 100, 92),
    (196, 80, 72), (90, 160, 175), (120, 175, 190), (150, 150, 160),
    (90, 160, 175), (120, 175, 190), (205, 190, 80), (215, 205, 110),
]


def prisms_from(data):
    """geometry.json → [(footprint[(x,y)...], z0, z1, stage_idx)]"""
    el = data["elements"]
    params = data.get("params", {})
    out = []

    def stage_of(cat, rec):
        for i, (_, pred) in enumerate(STAGES):
            if pred(cat, rec):
                return i
        return None

    for rec in el.get("column", []):
        si = stage_of("column", rec)
        if si is None or rec.get("kind") != "polyline":
            continue
        z0 = float(rec.get("z_base", 0))
        h = float(rec.get("overrides", {}).get("height", params.get("column", {}).get("height", 3000)))
        out.append((rec["points"], z0, z0 + h, si))

    for rec in el.get("wall", []):
        si = stage_of("wall", rec)
        if si is None:
            continue
        cl = rec.get("centerline") or rec.get("points") or []
        if len(cl) < 2:
            continue
        w = float(rec.get("overrides", {}).get("width")
                  or rec.get("width_detected")
                  or params.get("wall", {}).get("width", 200))
        z0 = float(rec.get("z_base", 0))
        h = float(rec.get("overrides", {}).get("height", params.get("wall", {}).get("height", 2800)))
        for a, b in zip(cl, cl[1:]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            L = math.hypot(dx, dy)
            if L < 1e-6:
                continue
            nx, ny = -dy / L * w / 2, dx / L * w / 2
            quad = [(a[0] + nx, a[1] + ny), (b[0] + nx, b[1] + ny),
                    (b[0] - nx, b[1] - ny), (a[0] - nx, a[1] - ny)]
            out.append((quad, z0, z0 + h, si))

    for rec in el.get("slab", []):
        si = stage_of("slab", rec)
        if si is None or rec.get("kind") != "polyline":
            continue
        t = float(rec.get("overrides", {}).get("thickness", params.get("slab", {}).get("thickness", 200)))
        z1 = float(rec.get("z_base", 0))            # 슬래브 z_base = 상단
        out.append((rec["points"], z1 - t, z1, si))
    return out


def faces_of(fp, z0, z1):
    """프리즘 → (면 폴리곤 3D, 밝기계수) 목록"""
    top = [(x, y, z1) for x, y in fp]
    bot = [(x, y, z0) for x, y in fp]
    fs = [(top, 1.00)]
    n = len(fp)
    for i in range(n):
        j = (i + 1) % n
        fs.append(([bot[i], bot[j], top[j], top[i]], 0.72 if i % 2 else 0.86))
    return fs


TILT = 0.50            # 수직 압축(아이소메트릭 느낌)


def _raw_proj(p, c, ca, sa):
    x, y, z = p[0] - c[0], p[1] - c[1], p[2] - c[2]
    return (x * ca - y * sa, -((x * sa + y * ca) * TILT + z))


def fit_camera(prisms, azs, size, margin=0.90):
    """전 회전구간을 통틀어 화면에 들어오도록 scale/offset 을 한 번만 고정(카메라 흔들림 없음)."""
    W, H = size
    xs = [p[0] for fp, _, _, _ in prisms for p in fp]
    ys = [p[1] for fp, _, _, _ in prisms for p in fp]
    zs = [z for _, z0, z1, _ in prisms for z in (z0, z1)]
    c = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2)
    corners = [(x, y, z) for x in (min(xs), max(xs)) for y in (min(ys), max(ys)) for z in (min(zs), max(zs))]
    u0 = v0 = 1e18
    u1 = v1 = -1e18
    for az in azs:
        ca, sa = math.cos(az), math.sin(az)
        for p in corners:
            u, v = _raw_proj(p, c, ca, sa)
            u0, u1 = min(u0, u), max(u1, u)
            v0, v1 = min(v0, v), max(v1, v)
    scale = min(W * margin / (u1 - u0), H * margin * 0.86 / (v1 - v0))
    ox = W / 2 - (u0 + u1) / 2 * scale
    oy = H / 2 + H * 0.045 - (v0 + v1) / 2 * scale
    return c, scale, ox, oy


def render(prisms, upto, az, size, cam, title, sub):
    W, H = size
    img = Image.new("RGB", (W, H), (250, 250, 252))
    dr = ImageDraw.Draw(img, "RGBA")
    c, scale, ox, oy = cam
    cx, cy, cz = c
    ca, sa = math.cos(az), math.sin(az)

    def proj(p):
        u, v = _raw_proj(p, c, ca, sa)
        return (ox + u * scale, oy + v * scale)

    polys = []
    for fp, z0, z1, si in prisms:
        if si > upto:
            continue
        base = STAGE_COLOR[si]
        fresh = (si == upto)
        for f3, shade in faces_of(fp, z0, z1):
            depth = sum((p[0] - cx) * sa + (p[1] - cy) * ca for p in f3) / len(f3) - \
                    sum(p[2] for p in f3) / len(f3) * 0.001
            col = tuple(min(255, int(v * shade + (60 if fresh else 0))) for v in base)
            polys.append((depth, [proj(p) for p in f3], col))

    polys.sort(key=lambda t: -t[0])
    for _, pts, col in polys:
        dr.polygon(pts, fill=col, outline=(40, 45, 55))

    try:
        f1 = ImageFont.truetype(r"C:\Windows\Fonts\malgunbd.ttf", int(H * 0.045))
        f2 = ImageFont.truetype(r"C:\Windows\Fonts\malgun.ttf", int(H * 0.028))
    except Exception:
        f1 = f2 = ImageFont.load_default()
    dr.rectangle([0, 0, W, int(H * 0.115)], fill=(255, 255, 255, 225))
    dr.text((int(W * 0.025), int(H * 0.018)), title, fill=(25, 30, 40), font=f1)
    dr.text((int(W * 0.025), int(H * 0.070)), sub, fill=(95, 105, 120), font=f2)
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("geometry")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--frames-per-stage", type=int, default=30)
    ap.add_argument("--project", default="", help="자막에 표기할 현장명(선택)")
    args = ap.parse_args()

    data = json.load(open(args.geometry, encoding="utf-8"))
    prisms = prisms_from(data)
    print(f"프리즘 {len(prisms)}개, 단계 {len(STAGES)}개")

    W = args.width
    Hh = int(W * 9 / 16)
    total = len(STAGES) * args.frames_per_stage
    # 아이소메트릭 유지 구간만 회전(정면 0°를 지나 평면적으로 보이는 것 방지)
    AZ0, AZ1 = 25.0, 65.0
    azs = [math.radians(AZ0 + (AZ1 - AZ0) * i / max(1, total - 1)) for i in range(total)]
    cam = fit_camera(prisms, azs, (W, Hh))

    tmp = tempfile.mkdtemp(prefix="anim4d_")
    n = 0
    for si, (name, _) in enumerate(STAGES):
        cnt = sum(1 for p in prisms if p[3] == si)
        cum = sum(1 for p in prisms if p[3] <= si)
        for k in range(args.frames_per_stage):
            img = render(prisms, si, azs[n], (W, Hh), cam,
                         name, f"부재 {cnt}개 시공  ·  누적 {cum}개" + (f"  ·  {args.project}" if args.project else ""))
            img.save(os.path.join(tmp, f"f{n:05d}.png"))
            n += 1
        print(f"  {name}: +{cnt} (누적 {cum})")

    out = args.out or os.path.splitext(args.geometry)[0] + "_4D시공.mp4"
    ff = shutil.which("ffmpeg") or r"C:\ffmpeg\ffmpeg-master-latest-win64-gpl\bin\ffmpeg.EXE"
    cmd = [ff, "-y", "-framerate", str(args.fps), "-i", os.path.join(tmp, "f%05d.png"),
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", out]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        print("[ERROR] ffmpeg 실패:", r.stderr.decode("utf-8", "ignore")[-800:])
        sys.exit(1)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"완료: {out}  ({n}프레임, {n/args.fps:.1f}초)")


if __name__ == "__main__":
    main()
