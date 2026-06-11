import sys
sys.path.append("c:/AI program/3D Modeling/mep-parser")
import FreeCAD as App
import json
import freecad_builder

doc = App.ActiveDocument
with open("c:/AI program/3D Modeling/mep-parser/temp_geometry.json", "r", encoding="utf-8") as f:
    data = json.load(f)

walls = data.get("walls", [])

open_walls = []
for i, el in enumerate(walls):
    if el.get("closed") or el.get("pairing") == "closed":
        continue
    if el.get("kind") != "polyline":
        continue
    open_walls.append(el)
    
print(f"open_walls: {len(open_walls)}")
chains = freecad_builder._chain_walls(open_walls)
print(f"chains: {len(chains)}")

out = f"walls: {len(walls)}, open: {len(open_walls)}, chains: {len(chains)}\n"
if chains:
    out += f"First chain pts: {chains[0][1]}\n"

with open("c:/AI program/3D Modeling/mep-parser/_out.txt", "w") as f:
    f.write(out)
