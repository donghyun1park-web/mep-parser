import json
import ezdxf
from fix_dxf_layers import classify_layer

def run():
    with open("c:/AI program/3D Modeling/mep-parser/fc_lines_dump.json", "r", encoding="utf-8") as f:
        data = json.load(f)
        
    doc = ezdxf.new('R2010')
    msp = doc.modelspace()
    
    wall_cnt = 0
    col_cnt = 0
    ignored = 0
    
    for item in data:
        layer = item["layer"]
        cat = classify_layer(layer)
        
        if cat == "ignore":
            ignored += 1
            continue
            
        # For dxf_parser to work easily, map cat to A-WALL or Block_C_
        target_layer = "A-WALL"
        if cat == "column":
            target_layer = "Block_C_AI"
            col_cnt += 1
        else:
            wall_cnt += 1
            
        if item["type"] == "Line":
            msp.add_line(item["p1"], item["p2"], dxfattribs={"layer": target_layer})
        elif item["type"] == "Circle":
            msp.add_circle(item["center"], item["radius"], dxfattribs={"layer": target_layer})
            
    doc.saveas("temp_export_fixed.dxf")
    print(f"Exported to temp_export_fixed.dxf: {wall_cnt} walls, {col_cnt} columns. Ignored {ignored}.")

if __name__ == "__main__":
    run()
