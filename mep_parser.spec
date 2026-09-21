# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 스펙 — MEP-Parser GUI 단일 .exe (Python 설치 불필요).
빌드: py -3.11 -m PyInstaller mep_parser.spec --noconfirm   (또는 build_exe.bat)
산출: dist/MEP-Parser.exe  (onefile, 윈도우 GUI)

동봉 리소스(런타임에 resource_path/_MEIPASS 로 해석):
  layer_map.csv, block_map.csv, freecad_builder.py, sample_plan.dxf, vendor/*.js
납품 IFC(`ifc_builder.py` + ifcopenshell)는 반드시 들어가야 한다 — 빌드 PC 에 설치할 것.
heavy/optional(matplotlib·anthropic·vision_classify)은 제외 — 코드가 graceful 폴백.
"""
from PyInstaller.utils.hooks import collect_all

datas = [
    ('layer_map.csv', '.'),
    ('block_map.csv', '.'),
    ('freecad_builder.py', '.'),     # freecadcmd 가 외부 프로세스로 읽음(있을 때만)
    ('geom_contract.py', '.'),
    ('verify.py', '.'),
    ('artifact_validation.py', '.'),
    ('blender_builder.py', '.'),
    ('blender_verify.py', '.'),
    ('sample_plan.dxf', '.'),        # --selftest 스모크용
    ('vendor/three.module.js', 'vendor'),
    ('vendor/OrbitControls.js', 'vendor'),
    ('vendor/edit_geometry.js', 'vendor'),
    ('frontend/built', 'frontend/built'),
]
binaries = []
hiddenimports = ['preview', 'dxf_parser', 'element_id']
hiddenimports += ['edit_review', 'project_store', 'project_server', 'freecad_runner']
hiddenimports += ['source_drawing', 'ifc_builder', 'construction_rules', 'boq_export']
hiddenimports += ['mep_paths', 'mep_profile', 'mep_setup_ui', 'blender_runner', 'blender_builder', 'blender_verify']

# ezdxf/shapely 는 동적 import·바이너리(GEOS) 의존 → 전체 수집.
# ★ `ifcopenshell` 은 2026-09-21 부터 **납품 경로**다(`ifc_builder.py`) — 빠지면 현장 PC 에서
#   '납품 검증 빌드 (IFC)' 가 죽는다. `--selftest` 가 import 까지는 보지만 빌드는 안 돌린다.
#   설치 안 된 PC 에서도 스펙 자체는 돌아가야 하므로(빌드 중단 방지) 있을 때만 수집한다.
for _pkg in ('ezdxf', 'shapely', 'ifcopenshell', 'numpy'):
    try:
        _d, _b, _h = collect_all(_pkg)
    except Exception as _exc:
        print(f"[spec] {_pkg} 수집 안 됨({_exc}) — 이 .exe 에는 안 들어간다")
        continue
    datas += _d
    binaries += _b
    hiddenimports += _h

a = Analysis(
    ['mep_gui.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'anthropic', 'vision_classify',
              'PIL', 'IPython', 'pytest', 'notebook', 'pandas',
              # The application uses Tk; optional ezdxf Qt viewers are not shipped.
              'PySide6', 'shiboken6', 'PySide2', 'shiboken2', 'PyQt6', 'PyQt5', 'wx'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='MEP-Parser',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,                # 윈도우 GUI(콘솔 없음). 디버그 시 True 로.
    disable_windowed_traceback=False,
    argv_emulation=False,
)
