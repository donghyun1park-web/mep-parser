"""FreeCAD 내부에서 쓰는 헬퍼. 라이브 자연어 기능(freecad_live_addon)이 유일한 소비자다.

GUI 매크로 커맨드셋(commands.py/ui.py)은 dxf_parser 파이프라인에 밀려 제거됐다
(실측: 같은 도면에서 벽 211 vs 677, 기둥 21 vs 93). freecad_utils 만 남는다.
"""
