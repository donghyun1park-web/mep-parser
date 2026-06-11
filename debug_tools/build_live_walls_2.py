import sys
sys.path.append("c:/AI program/3D Modeling/mep-parser")
import FreeCAD as App
import json
import freecad_builder
import traceback
import importlib

importlib.reload(freecad_builder)

z_offset = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0
prefix = sys.argv[2] if len(sys.argv) > 2 else ""

doc = App.ActiveDocument
with open("c:/AI program/3D Modeling/mep-parser/temp_geometry.json", "r", encoding="utf-8") as f:
    data = json.load(f)

for cat in ["wall", "column", "slab", "opening", "zone"]:
    for el in data.get("elements", {}).get(cat, []):
        el["z_base"] = float(el.get("z_base", 0.0)) + z_offset
        if prefix:
            handle = el.get("handle", "")
            el["handle"] = f"{prefix}_{handle}"

walls = data.get("elements", {}).get("wall", [])
columns = data.get("elements", {}).get("column", [])
params = data.get("params", {})

out = f"Loaded {len(walls)} walls and {len(columns)} columns.\n"

try:
    # Build in smaller batches to avoid timeout
    BATCH = 500
    walls_created = []
    wall_idx_map_global = {}
    total_walls_built = 0
    for start in range(0, len(walls), BATCH):
        batch = walls[start:start+BATCH]
        w_objs, w_idx_map, _ = freecad_builder.build_walls(doc, batch, params)
        for i, obj in w_idx_map.items():
            wall_idx_map_global[start + i] = obj
        total_walls_built += len(batch)
        out += f"  Built wall batch {start}-{start+len(batch)}\n"
    
    if columns:
        freecad_builder.build_columns(doc, columns, params)
        out += f"  Built {len(columns)} columns\n"
        
    doc.recompute()
    
    openings = data.get("elements", {}).get("opening", [])
    if openings:
        n_voids, n_leaf, log = freecad_builder.build_openings(doc, openings, wall_idx_map_global, params)
        out += f"  Built {n_voids} openings ({n_leaf} leaves)\n"
        out += log
    
    if App.GuiUp:
        import FreeCADGui as Gui
        Gui.updateGui()
    out += "Success\n"
except Exception as e:
    out += traceback.format_exc()

with open("c:/AI program/3D Modeling/mep-parser/_out.txt", "w") as f:
    f.write(out)
