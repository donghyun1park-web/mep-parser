import sys
sys.path.append("c:/AI program/3D Modeling/mep-parser")
import FreeCAD as App
import json
import freecad_builder
import traceback

doc = App.newDocument("LiveAI_Doc")
with open("c:/AI program/3D Modeling/mep-parser/temp_geometry.json", "r", encoding="utf-8") as f:
    data = json.load(f)

walls = data.get("walls", [])
params = data.get("parameters", {})

out = ""
try:
    freecad_builder.build_walls(doc, walls, params)
    out += "Success\n"
except Exception as e:
    out += traceback.format_exc()

with open("c:/AI program/3D Modeling/mep-parser/_out.txt", "w") as f:
    f.write(out)
