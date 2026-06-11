import FreeCAD as App

doc = App.ActiveDocument
out = f"Active Doc: {doc.Name if doc else 'None'}\n"
if doc:
    walls = [obj for obj in doc.Objects if "Wall" in obj.TypeId or "Wall" in obj.Name or "Wall" in obj.Label]
    out += f"Total Wall objects: {len(walls)}\n"
    if walls:
        out += f"Sample Wall: {walls[0].Name} / {walls[0].Label} / {walls[0].TypeId}\n"

with open("c:/AI program/3D Modeling/mep-parser/_out.txt", "w") as f:
    f.write(out)
