import FreeCAD as App
import json
import freecad_builder

doc = App.ActiveDocument
if not doc:
    doc = App.newDocument("LiveAI_Doc")

with open("c:/AI program/3D Modeling/mep-parser/temp_geometry.json", "r", encoding="utf-8") as f:
    data = json.load(f)

walls = data.get("walls", [])
params = data.get("parameters", {})

print(f"Loaded {len(walls)} walls from temp_geometry.json")

freecad_builder.build_walls(doc, walls, params)
doc.recompute()
if App.GuiUp:
    import FreeCADGui as Gui
    Gui.updateGui()
print("Walls built successfully!")
