# -*- coding: utf-8 -*-
"""의존성 없는 테스트 러너 — `python tests/run_all.py` 로 그냥 돈다.

pytest 가 있으면 `pytest tests/` 로도 수집된다(테스트는 순수 assert 함수).
자체 assert 프레임워크를 만들지 말 것 — 잘못 만든 pytest 가 될 뿐이다.
"""
import importlib.util
import inspect
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def load(path):
    name = os.path.splitext(os.path.basename(path))[0]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    files = sorted(f for f in os.listdir(HERE)
                   if f.startswith("test_") and f.endswith(".py"))
    npass = nfail = 0
    fails = []
    for fn in files:
        try:
            mod = load(os.path.join(HERE, fn))
        except Exception:
            nfail += 1
            fails.append((fn, "<import>", traceback.format_exc()))
            print(f"  FAIL {fn} <import>")
            continue
        for name, fn_obj in sorted(vars(mod).items()):
            if not (name.startswith("test_") and inspect.isfunction(fn_obj)):
                continue
            if inspect.signature(fn_obj).parameters:      # 픽스처 인자 있는 것은 pytest 전용
                continue
            try:
                fn_obj()
                npass += 1
            except Exception:
                nfail += 1
                fails.append((fn, name, traceback.format_exc()))
                print(f"  FAIL {fn}::{name}")
    for fn, name, tb in fails:
        print("\n" + "=" * 70)
        print(f"{fn}::{name}")
        print(tb.rstrip())
    print("\n" + "-" * 70)
    print(f"통과 {npass} / 실패 {nfail}")
    return 1 if nfail else 0


if __name__ == "__main__":
    raise SystemExit(main())
