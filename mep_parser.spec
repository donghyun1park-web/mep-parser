# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 스펙 — MEP-Parser GUI 단일 .exe (Python 설치 불필요).
빌드: py -3.11 -m PyInstaller mep_parser.spec --noconfirm   (또는 build_exe.bat)
산출: dist/MEP-Parser.exe  (onefile, 윈도우 GUI)

동봉 리소스(런타임에 resource_path/_MEIPASS 로 해석):
  layer_map.csv, block_map.csv, freecad_builder.py, sample_plan.dxf, vendor/*.js
heavy/optional(matplotlib·anthropic·vision_classify)은 제외 — 코드가 graceful 폴백.
"""
from PyInstaller.utils.hooks import collect_all

datas = [
    ('layer_map.csv', '.'),
    ('block_map.csv', '.'),
    ('freecad_builder.py', '.'),     # freecadcmd 가 외부 프로세스로 읽음(있을 때만)
    ('sample_plan.dxf', '.'),        # --selftest 스모크용
    ('vendor/three.module.js', 'vendor'),
    ('vendor/OrbitControls.js', 'vendor'),
]
binaries = []
hiddenimports = ['preview', 'dxf_parser', 'element_id']

# ezdxf/shapely 는 동적 import·바이너리(GEOS) 의존 → 전체 수집
for _pkg in ('ezdxf', 'shapely'):
    _d, _b, _h = collect_all(_pkg)
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
              'PIL', 'IPython', 'pytest', 'notebook', 'pandas'],
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
