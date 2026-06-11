import FreeCAD as App
doc = App.ActiveDocument
walls = [obj for obj in doc.Objects if "Wall_" in obj.Name]
out = f"Total new walls in doc: {len(walls)}\n"
if walls:
    out += f"First wall name: {walls[0].Name}, Label: {walls[0].Label}, Base: {walls[0].Base.Name if walls[0].Base else 'None'}\n"
    if walls[0].Base and hasattr(walls[0].Base, "Shape"):
        out += f"First wall base length: {walls[0].Base.Shape.Length}\n"

with open("c:/AI program/3D Modeling/mep-parser/_out.txt", "w") as f:
    f.write(out)
