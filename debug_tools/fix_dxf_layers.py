"""
fix_dxf_layers.py - DXF 레이어 정리 (화이트리스트 기반)

벽체 대상: Line*, Polyline*, _wall_base*, Arc*  → "A-WALL"
기둥 대상: Block_C_*                              → 원래 레이어 유지
무시 대상: Block_ELEV*, Block_SSD*, Block_core*, Block_101*, Block_102*,
           Block_103*, Block_BPOINT*, Block_SD_*, Circle*, 기타 Block_*
"""
import ezdxf

# 무시할 Block_ 접두사 목록
IGNORE_BLOCK_PREFIXES = [
    "Block_ELEV",
    "Block_SSD",
    "Block_core",
    "Block_101",
    "Block_102",
    "Block_103",
    "Block_BPOINT",
    "Block_SD_",
]


def classify_layer(layer_name):
    """레이어 이름으로 분류: 'wall', 'column', 'ignore'"""
    # 1. 기둥: Block_C_ 로 시작하거나 #CHK_U_250212
    if layer_name.startswith("Block_C_") or layer_name.upper().startswith("#CHK_U"):
        return "column"

    # 2. 무시: 특정 Block_ 접두사
    for prefix in IGNORE_BLOCK_PREFIXES:
        if layer_name.startswith(prefix):
            return "ignore"

    # 3. 기타 Block_ (한글 이름 등) → 무시
    if layer_name.startswith("Block_"):
        return "ignore"

    # 4. Circle → 무시
    if layer_name.startswith("Circle"):
        return "ignore"

    # 5. 사용자 지정 무시 규칙 (대소문자 구분 없이 처리)
    layer_lower = layer_name.lower()
    if "배수판" in layer_lower:
        return "ignore"
    if "a-cen-sub" in layer_lower:
        return "ignore"
    if "a-door" in layer_lower:
        return "ignore"
    if layer_lower == "a-cen":
        return "ignore"
    if "a-hat" in layer_lower:
        return "ignore"

    # 6. 나머지 (Line*, Polyline*, _wall_base*, Arc*) → 벽체
    return "wall"


def fix_layers(in_file, out_file):
    doc = ezdxf.readfile(in_file)
    msp = doc.modelspace()

    to_delete = []
    wall_count = 0
    col_count = 0
    ignore_count = 0

    for e in msp:
        layer = e.dxf.layer
        cat = classify_layer(layer)

        if cat == "wall":
            e.dxf.layer = "A-WALL"
            wall_count += 1
        elif cat == "column":
            # 원래 레이어 이름 유지
            col_count += 1
        else:  # ignore
            to_delete.append(e)
            ignore_count += 1

    # 무시 대상 삭제
    for e in to_delete:
        msp.delete_entity(e)

    doc.saveas(out_file)
    print(f"Wall entities: {wall_count}")
    print(f"Column entities: {col_count}")
    print(f"Ignored (deleted): {ignore_count}")
    print(f"Saved to {out_file}")


if __name__ == "__main__":
    fix_layers("temp_export.dxf", "temp_export_fixed.dxf")
