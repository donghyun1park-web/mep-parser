import FreeCAD as App
import importDXF

doc = App.ActiveDocument
objs = [obj for obj in doc.Objects if obj.TypeId == "Part::Feature" and "Wall" not in obj.Name]
out = f"Found {len(objs)} objects for export\n"

try:
    importDXF.export(objs, "c:/AI program/3D Modeling/mep-parser/temp_export.dxf")
    out += "Export successful!\n"
except Exception as e:
    out += f"Export failed: {e}\n"

with open("c:/AI program/3D Modeling/mep-parser/_out.txt", "w") as f:
    f.write(out)
