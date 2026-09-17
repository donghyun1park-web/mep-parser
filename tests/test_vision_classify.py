# -*- coding: utf-8 -*-
"""Vision 분류는 모델을 바꿔도 **답을 읽고, 예전 모델의 캐시를 재사용하지 않는다.**

claude-opus-5 는 thinking 이 기본 ON 이라 `content[0]` 이 text 블록이 아니다 — `content[0].text` 로 읽으면
AttributeError 가 `except Exception` 에 먹혀 **모든 호출이 조용히 None** 이 된다.
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import vision_classify as V


def _fake_anthropic(calls):
    class _Msgs:
        def create(self, **kw):
            calls.append(kw)
            return types.SimpleNamespace(content=[
                types.SimpleNamespace(type="thinking", thinking=""),
                types.SimpleNamespace(type="text", text='```json\n{"category":"wall","subtype":null,'
                                                        '"reason":"두 줄 평행선","confidence":0.9}\n```'),
            ])

    class _Client:
        def __init__(self, api_key=None):
            self.messages = _Msgs()

    return types.SimpleNamespace(Anthropic=_Client)


def test_vision_one_reads_text_block_after_thinking_and_uses_vision_model():
    calls = []
    saved = sys.modules.get("anthropic")
    sys.modules["anthropic"] = _fake_anthropic(calls)
    try:
        res = V._vision_one("AAAA", "key")
    finally:
        if saved is None:
            sys.modules.pop("anthropic", None)
        else:
            sys.modules["anthropic"] = saved
    assert res == ("wall", None, "두 줄 평행선", 0.9), res
    assert calls[0]["model"] == V.VISION_MODEL == "claude-opus-5"
    assert calls[0]["output_config"] == {"effort": "low"}


def test_vision_cache_key_includes_model():
    """haiku 시절 `.ai_cache.json` 의 `vis|<layer>` 항목은 새 모델에서 재사용되지 않는다."""
    saved = (V.render_dxf_to_png, V._bbox_of_recs, V._crop_b64, V._vision_one)
    V.render_dxf_to_png = lambda p: (b"", None, (1, 1))
    V._bbox_of_recs = lambda recs: (0, 0, 1, 1)
    V._crop_b64 = lambda *a: "AAAA"
    V._vision_one = lambda b64, key: ("column", None, "r", 0.7)
    try:
        cache = {"vis|A-X": {"cat": "wall", "conf": 0.9}}   # 예전 키
        sug = [{"source": "layer", "layer": "A-X", "name_score": 0, "llm_confidence": 0}]
        n = V.vision_fallback("x.dxf", sug, {"A-X": [{}]}, api_key="k", cache=cache)
    finally:
        V.render_dxf_to_png, V._bbox_of_recs, V._crop_b64, V._vision_one = saved
    assert n == 1 and sug[0]["vision_guess"] == "column"
    assert "vis|claude-opus-5|A-X" in cache and cache["vis|A-X"]["cat"] == "wall"
