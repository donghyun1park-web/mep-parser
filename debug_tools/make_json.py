import sys
import os
import json
import dxf_parser as _P
import contextlib
import io

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else r"C:\AI program\architectural_timelapse_phase16\input\drawings\지하4층 건축평면도.dxf"
    LAYER_MAP = "layer_map.csv"
    BLOCK_MAP = "block_map.csv"
    rules = _P.load_layer_map(LAYER_MAP) if os.path.exists(LAYER_MAP) else _P.DEFAULT_LAYER_RULES
    brules = _P.load_layer_map(BLOCK_MAP) if os.path.exists(BLOCK_MAP) else _P.DEFAULT_BLOCK_RULES
    
    print(f"Parsing DXF locally: {path}")
    data = _P.parse(path, rules, brules, use_ai=False)
    
    out_path = r"c:\AI program\3D Modeling\mep-parser\temp_geometry.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"{out_path} saved.")

if __name__ == "__main__":
    main()
