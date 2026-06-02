"""
vision_classify.py  —  [Phase C] Vision 폴백 (옵션)

텍스트 분류(레이어명+블록명+기하)로 해결 못한 미매핑 레이어/블록을,
DXF를 이미지로 렌더한 뒤 Claude Vision이 '보고' 분류하는 폴백.

설계 원칙 (프로젝트 불변 제약 준수):
- Vision은 category/subtype/confidence 만 반환. **좌표·FreeCAD 코드 생성 금지.**
  기하는 기존 ezdxf 추출값을 그대로 사용한다.
- 옵션 의존(anthropic + matplotlib + ezdxf.addons.drawing + Pillow).
  하나라도 없으면 RuntimeError → 호출부(parse)가 graceful 스킵.
- 재현성: 결과는 dxf_parser 의 AI 캐시에 sig-key로 저장 → 동일 도면 동일 결과.

흐름:
  1. render_dxf_to_png(dxf): modelspace 를 PNG로 렌더 + DXF→픽셀 아핀변환 반환.
  2. 각 저신뢰 제안의 레코드 bbox → 픽셀 crop → Vision 분류.
  3. suggestion 에 vision_guess/vision_subtype/vision_confidence/vision_reason 기록.
"""
import base64
import io
import os


_VALID = ("wall", "column", "slab", "zone", "opening",
          "pipe", "duct", "tray", "equipment")

_VISION_SYSTEM = (
    "You are an architectural drawing analyst. You are shown a CROPPED region of a "
    "2D floor plan (black lines on white). Identify the single building element the "
    "crop most likely represents. Reply ONLY valid JSON: "
    "{\"category\":\"<wall|column|slab|zone|opening|pipe|duct|tray|equipment>\","
    "\"subtype\":\"<door|window|null>\",\"reason\":\"<one short Korean sentence>\","
    "\"confidence\":<0.0-1.0>}. "
    "subtype only when category is opening. "
    "NEVER output coordinates or FreeCAD code. Classification only."
)


def _require(modname):
    try:
        return __import__(modname)
    except ImportError as e:
        raise RuntimeError(f"의존 모듈 없음: {modname} ({e})")


def render_dxf_to_png(dxf_path, max_px=1600):
    """modelspace → PNG bytes + transform(dxf_x,dxf_y -> px,py).
    matplotlib 백엔드 사용. 실패 시 RuntimeError."""
    _require("matplotlib")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import ezdxf
    from ezdxf.addons.drawing import RenderContext, Frontend
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()

    # DXF 범위
    xs, ys = [], []
    for e in msp:
        try:
            if e.dxftype() == "LINE":
                xs += [e.dxf.start.x, e.dxf.end.x]
                ys += [e.dxf.start.y, e.dxf.end.y]
            elif e.dxftype() in ("CIRCLE", "ARC"):
                c = e.dxf.center
                xs.append(c.x); ys.append(c.y)
        except Exception:
            pass
    if not xs or not ys:
        raise RuntimeError("DXF 좌표 범위 산출 실패")
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    w, h = xmax - xmin, ymax - ymin
    if w <= 0 or h <= 0:
        raise RuntimeError("DXF 범위 0")

    # 픽셀 크기(종횡비 유지, 긴 변 = max_px)
    if w >= h:
        px_w = max_px
        px_h = max(1, int(max_px * h / w))
    else:
        px_h = max_px
        px_w = max(1, int(max_px * w / h))

    fig = plt.figure(figsize=(px_w / 100.0, px_h / 100.0), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()
    try:
        ctx = RenderContext(doc)
        backend = MatplotlibBackend(ax)
        Frontend(ctx, backend).draw_layout(msp, finalize=True)
    except Exception as e:
        plt.close(fig)
        raise RuntimeError(f"렌더 실패: {e}")
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    fig.canvas.draw()  # transData 확정(aspect='equal' 자동조정 반영)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100)
    # 실제 렌더 픽셀 크기 + transData 캡처(aspect 보정 후 정확한 변환)
    fig_h_px = fig.canvas.get_width_height()[1]
    fig_w_px = fig.canvas.get_width_height()[0]
    trans_data = ax.transData
    # display 좌표(픽셀, 원점 좌하단) 샘플 추출 후 close
    import numpy as _np

    def _to_disp(x, y):
        return trans_data.transform((x, y))
    # 변환 파라미터를 닫기 전에 두 기준점으로 선형화
    d00 = _to_disp(xmin, ymin)
    d11 = _to_disp(xmax, ymax)
    plt.close(fig)
    buf.seek(0)
    png = buf.read()

    sx = (d11[0] - d00[0]) / (xmax - xmin) if xmax != xmin else 1.0
    sy = (d11[1] - d00[1]) / (ymax - ymin) if ymax != ymin else 1.0

    def transform(x, y):
        """DXF 좌표 → 이미지 픽셀 (좌상단 원점, y 아래로)."""
        disp_x = d00[0] + (x - xmin) * sx
        disp_y = d00[1] + (y - ymin) * sy
        return disp_x, fig_h_px - disp_y  # y 뒤집기(이미지 좌상단 원점)

    return png, transform, (fig_w_px, fig_h_px)


def _crop_b64(png, transform, px_size, bbox_dxf, margin_px=40):
    """DXF bbox 영역을 PNG에서 crop → base64 PNG. Pillow 사용."""
    _require("PIL")
    from PIL import Image
    img = Image.open(io.BytesIO(png)).convert("RGB")
    (x0, y0, x1, y1) = bbox_dxf
    p0 = transform(x0, y0)
    p1 = transform(x1, y1)
    left = max(0, int(min(p0[0], p1[0]) - margin_px))
    right = min(px_size[0], int(max(p0[0], p1[0]) + margin_px))
    top = max(0, int(min(p0[1], p1[1]) - margin_px))
    bot = min(px_size[1], int(max(p0[1], p1[1]) + margin_px))
    if right - left < 8:
        right = min(px_size[0], left + 8)
    if bot - top < 8:
        bot = min(px_size[1], top + 8)
    crop = img.crop((left, top, right, bot))
    out = io.BytesIO()
    crop.save(out, format="PNG")
    return base64.b64encode(out.getvalue()).decode("ascii")


def _bbox_of_recs(recs):
    xs, ys = [], []
    for r in recs:
        if r.get("kind") == "circle":
            c = r.get("center", [0, 0]); rad = r.get("radius", 0)
            xs += [c[0] - rad, c[0] + rad]; ys += [c[1] - rad, c[1] + rad]
        else:
            for p in r.get("points", []):
                xs.append(p[0]); ys.append(p[1])
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _vision_one(b64png, api_key):
    """crop 1개 → Claude Vision 분류 (cat, subtype, reason, conf) or None."""
    try:
        import anthropic
    except ImportError:
        raise RuntimeError("anthropic SDK 없음")
    import json as _json
    import re as _re
    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=160,
            system=_VISION_SYSTEM,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/png", "data": b64png}},
                {"type": "text", "text": "이 영역의 건축 요소를 분류하세요."},
            ]}],
        )
        raw = msg.content[0].text.strip()
        raw = _re.sub(r"```[a-z]*\n?", "", raw).strip().rstrip("`").strip()
        p = _json.loads(raw)
        cat = p.get("category", "").lower()
        if cat not in _VALID:
            return None
        sub = p.get("subtype")
        sub = sub.lower() if isinstance(sub, str) and sub.lower() in ("door", "window") else None
        return (cat, sub, str(p.get("reason", ""))[:120], float(p.get("confidence", 0.5)))
    except Exception:
        return None


def vision_fallback(dxf_path, suggestions, unmapped_recs, api_key=None, cache=None):
    """저신뢰 제안만 Vision 분류 → suggestion 에 vision_* 필드 기록.
    레이어 제안만 대상(블록은 explode 기하가 작아 crop 효과 낮음 → 후속).
    실패/의존없음 → RuntimeError(호출부 graceful 스킵)."""
    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY 없음")
    if cache is None:
        cache = {}

    # 저신뢰 대상: 이름/LLM 둘 다 약한 레이어 제안
    targets = [s for s in suggestions
               if s.get("source") == "layer"
               and float(s.get("name_score") or 0) < 0.6
               and float(s.get("llm_confidence") or 0) < 0.8
               and s["layer"] in unmapped_recs]
    if not targets:
        return 0

    png, transform, px_size = render_dxf_to_png(dxf_path)
    n = 0
    for s in targets:
        key = "vis|" + s["layer"]
        cached = cache.get(key)
        if cached:
            res = (cached["cat"], cached.get("subtype"),
                   cached.get("reason", ""), cached.get("conf", 0.5))
        else:
            bbox = _bbox_of_recs(unmapped_recs[s["layer"]])
            if not bbox:
                continue
            try:
                b64 = _crop_b64(png, transform, px_size, bbox)
            except Exception:
                continue
            res = _vision_one(b64, api_key)
            if res:
                cache[key] = {"cat": res[0], "subtype": res[1],
                              "reason": res[2], "conf": res[3]}
        if res:
            s["vision_guess"] = res[0]
            s["vision_subtype"] = res[1]
            s["vision_reason"] = res[2]
            s["vision_confidence"] = round(res[3], 2)
            n += 1
    return n
