import FreeCAD as App

doc = App.ActiveDocument
out = ""
if doc:
    walls = [obj for obj in doc.Objects if "Wall" in obj.Name or "Wall" in obj.Label]
    columns = [obj for obj in doc.Objects if "Column" in obj.Name or "Column" in obj.Label]
    out += f"Total Wall objects: {len(walls)}\n"
    out += f"Total Column objects: {len(columns)}\n"
else:
    out += "No active document.\n"

with open("c:/AI program/3D Modeling/mep-parser/_out.txt", "w") as f:
    f.write(out)
