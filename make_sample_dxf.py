"""
make_sample_dxf.py
간단한 평면도 샘플 DXF 생성기 (테스트용).
실무 도면 대신 파이프라인을 검증하기 위한 최소 예제.

레이어 규칙 (AIA/국내 관행 혼용 예시):
  A-WALL  : 벽 중심선 (LINE / LWPOLYLINE)
  A-COLS  : 기둥 (closed LWPOLYLINE 또는 CIRCLE)
  A-SLAB  : 슬래브/바닥 외곽선 (closed LWPOLYLINE)
  A-DOOR  : 문 위치 (CIRCLE 마커 - 위치만 표시)
단위: mm
"""
import ezdxf

doc = ezdxf.new("R2010", setup=True)
doc.units = ezdxf.units.MM
msp = doc.modelspace()

# 레이어 정의
for name, color in [("A-WALL", 1), ("A-COLS", 3), ("A-SLAB", 8), ("A-DOOR", 5), ("A-ZONE", 4)]:
    if name not in doc.layers:
        doc.layers.add(name, color=color)

# --- 슬래브 외곽선 (10000 x 8000 mm 박스) ---
msp.add_lwpolyline(
    [(0, 0), (10000, 0), (10000, 8000), (0, 8000)],
    close=True,
    dxfattribs={"layer": "A-SLAB"},
)

# --- 외벽 중심선 (슬래브 안쪽 100mm) ---
msp.add_lwpolyline(
    [(100, 100), (9900, 100), (9900, 7900), (100, 7900)],
    close=True,
    dxfattribs={"layer": "A-WALL"},
)

# --- 내벽 중심선 (방 분할) ---
msp.add_line((5000, 100), (5000, 7900), dxfattribs={"layer": "A-WALL"})
msp.add_line((100, 4000), (5000, 4000), dxfattribs={"layer": "A-WALL"})

# --- 기둥 4개 (400x400 closed polyline) ---
for cx, cy in [(2500, 2000), (2500, 6000), (7500, 2000), (7500, 6000)]:
    h = 200
    msp.add_lwpolyline(
        [(cx - h, cy - h), (cx + h, cy - h), (cx + h, cy + h), (cx - h, cy + h)],
        close=True,
        dxfattribs={"layer": "A-COLS"},
    )

# --- 문 위치 마커 (위치 메타데이터만) ---
for dx, dy in [(2500, 100), (5000, 2000)]:
    msp.add_circle((dx, dy), radius=450, dxfattribs={"layer": "A-DOOR"})

# --- 구역(방) 폴리곤 2개: 좌측실 / 우측실 ---
msp.add_lwpolyline(
    [(100, 100), (5000, 100), (5000, 7900), (100, 7900)],
    close=True, dxfattribs={"layer": "A-ZONE"},
)
msp.add_lwpolyline(
    [(5000, 100), (9900, 100), (9900, 7900), (5000, 7900)],
    close=True, dxfattribs={"layer": "A-ZONE"},
)

doc.saveas("sample_plan.dxf")
print("wrote sample_plan.dxf")


# =====================================================================
# sample_walls.dxf — 벽을 '양면 2선'으로 그린 실무형 샘플 (Phase 1 검증용)
#   외벽: 동심 사각형 2개(200mm 간격) → 4면이 평행선 쌍
#   내벽: 평행한 두 수직선(x=5000, 5200) → 1쌍
#   단독선: 짝 없는 벽선 1개 → pairing="single" + needs_review 시연
# =====================================================================
doc2 = ezdxf.new("R2010", setup=True)
doc2.units = ezdxf.units.MM
m2 = doc2.modelspace()
for name, color in [("A-WALL", 1), ("A-COLS", 3), ("A-SLAB", 8), ("A-ZONE", 4)]:
    if name not in doc2.layers:
        doc2.layers.add(name, color=color)

# 슬래브 외곽
m2.add_lwpolyline([(0, 0), (10000, 0), (10000, 8000), (0, 8000)],
                  close=True, dxfattribs={"layer": "A-SLAB"})

# 외벽: 양면 2선(동심 사각형, 200mm 두께)
m2.add_lwpolyline([(0, 0), (10000, 0), (10000, 8000), (0, 8000)],
                  close=True, dxfattribs={"layer": "A-WALL"})        # 외측 면
m2.add_lwpolyline([(200, 200), (9800, 200), (9800, 7800), (200, 7800)],
                  close=True, dxfattribs={"layer": "A-WALL"})        # 내측 면

# 내벽 칸막이: 평행한 두 수직선(200mm 간격)
m2.add_line((5000, 200), (5000, 7800), dxfattribs={"layer": "A-WALL"})
m2.add_line((5200, 200), (5200, 7800), dxfattribs={"layer": "A-WALL"})

# 단독선(짝 없음) → single 처리 시연
m2.add_line((1000, 4000), (4000, 4000), dxfattribs={"layer": "A-WALL"})

# 기둥 4개
for cx, cy in [(2500, 2000), (2500, 6000), (7500, 2000), (7500, 6000)]:
    h = 200
    m2.add_lwpolyline([(cx - h, cy - h), (cx + h, cy - h),
                       (cx + h, cy + h), (cx - h, cy + h)],
                      close=True, dxfattribs={"layer": "A-COLS"})

# 구역 2개
m2.add_lwpolyline([(0, 0), (5000, 0), (5000, 8000), (0, 8000)],
                  close=True, dxfattribs={"layer": "A-ZONE"})
m2.add_lwpolyline([(5000, 0), (10000, 0), (10000, 8000), (5000, 8000)],
                  close=True, dxfattribs={"layer": "A-ZONE"})

doc2.saveas("sample_walls.dxf")
print("wrote sample_walls.dxf")


# =====================================================================
# sample_blocks.dxf — 기둥/문/창을 BLOCK 참조(INSERT)로 삽입한 샘플 (Phase 2 검증용)
#   COL400  : 400x400 closed LWPOLYLINE 기둥 블록 (원점 기준) → INSERT 4개
#   DOOR-SW : 문짝 호 + 선으로 그린 문 블록 → INSERT 2개
#   회전/스케일 INSERT 포함 → virtual_entities() explode 검증
# =====================================================================
doc3 = ezdxf.new("R2010", setup=True)
doc3.units = ezdxf.units.MM
m3 = doc3.modelspace()
for name, color in [("A-WALL", 1), ("A-COLS", 3), ("A-SLAB", 8), ("A-DOOR", 5), ("A-ZONE", 4)]:
    if name not in doc3.layers:
        doc3.layers.add(name, color=color)

# --- 블록 정의: 기둥(원점 기준 400x400 closed polyline) ---
col_blk = doc3.blocks.new(name="COL400")
col_blk.add_lwpolyline([(-200, -200), (200, -200), (200, 200), (-200, 200)],
                       close=True, dxfattribs={"layer": "A-COLS"})

# --- 블록 정의: 문(900mm 개폐 호 + 문틀 선) ---
door_blk = doc3.blocks.new(name="DOOR-SW")
door_blk.add_line((0, 0), (900, 0), dxfattribs={"layer": "A-DOOR"})        # 문짝
door_blk.add_arc((0, 0), radius=900, start_angle=0, end_angle=90,
                 dxfattribs={"layer": "A-DOOR"})                            # 개폐 궤적

# 슬래브 외곽
m3.add_lwpolyline([(0, 0), (10000, 0), (10000, 8000), (0, 8000)],
                  close=True, dxfattribs={"layer": "A-SLAB"})

# 기둥 INSERT 4개 (블록 참조, 위치만 다름)
for cx, cy in [(2500, 2000), (2500, 6000), (7500, 2000), (7500, 6000)]:
    m3.add_blockref("COL400", (cx, cy), dxfattribs={"layer": "A-COLS"})

# 문 INSERT 2개 (하나는 회전 90°)
m3.add_blockref("DOOR-SW", (2000, 100), dxfattribs={"layer": "A-DOOR"})
m3.add_blockref("DOOR-SW", (5000, 4000),
                dxfattribs={"layer": "A-DOOR", "rotation": 90})

# 구역 2개
m3.add_lwpolyline([(0, 0), (5000, 0), (5000, 8000), (0, 8000)],
                  close=True, dxfattribs={"layer": "A-ZONE"})
m3.add_lwpolyline([(5000, 0), (10000, 0), (10000, 8000), (5000, 8000)],
                  close=True, dxfattribs={"layer": "A-ZONE"})

doc3.saveas("sample_blocks.dxf")
print("wrote sample_blocks.dxf")


# =====================================================================
# sample_mep.dxf — 배관/덕트/트레이/장비 샘플 (Phase 2.7 추출 검증용)
#   배관: 중심선 LINE (고저 z=2600) + 분기
#   덕트: 중심선 LWPOLYLINE (z=2800)
#   트레이: 중심선 LINE (z=3000)
#   장비: AHU 블록 INSERT
# =====================================================================
doc4 = ezdxf.new("R2010", setup=True)
doc4.units = ezdxf.units.MM
m4 = doc4.modelspace()
for name, color in [("M-PIPE", 1), ("M-DUCT", 2), ("E-TRAY", 4), ("M-EQUIP", 6), ("A-SLAB", 8)]:
    if name not in doc4.layers:
        doc4.layers.add(name, color=color)

# 장비 블록 정의 (AHU: 2000x1500 박스)
ahu = doc4.blocks.new(name="AHU-1")
ahu.add_lwpolyline([(-1000, -750), (1000, -750), (1000, 750), (-1000, 750)],
                   close=True, dxfattribs={"layer": "M-EQUIP"})

# 슬래브 외곽(참조)
m4.add_lwpolyline([(0, 0), (12000, 0), (12000, 9000), (0, 9000)],
                  close=True, dxfattribs={"layer": "A-SLAB"})

# 배관 중심선 (천장 아래 z=2600). 메인 + 분기
m4.add_line((1000, 1000, 2600), (11000, 1000, 2600), dxfattribs={"layer": "M-PIPE"})
m4.add_line((6000, 1000, 2600), (6000, 8000, 2600), dxfattribs={"layer": "M-PIPE"})

# 덕트 중심선 (z=2800, LWPOLYLINE 꺾임)
duct = m4.add_lwpolyline([(1000, 8000), (11000, 8000), (11000, 4000)],
                         dxfattribs={"layer": "M-DUCT"})
duct.dxf.elevation = 2800

# 케이블 트레이 (z=3000)
m4.add_line((1000, 4500, 3000), (11000, 4500, 3000), dxfattribs={"layer": "E-TRAY"})

# 장비 INSERT 2개 (AHU)
m4.add_blockref("AHU-1", (3000, 3000), dxfattribs={"layer": "M-EQUIP"})
m4.add_blockref("AHU-1", (9000, 6000), dxfattribs={"layer": "M-EQUIP"})

doc4.saveas("sample_mep.dxf")
print("wrote sample_mep.dxf")
