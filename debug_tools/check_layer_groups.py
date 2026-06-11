import FreeCAD as App

doc = App.ActiveDocument
out = ""
if doc:
    # Get all objects that have a Shape and are not Walls
    objs = [obj for obj in doc.Objects if hasattr(obj, "Shape") and "Wall" not in obj.Name and "Wall" not in obj.Label]
    out += f"Found {len(objs)} original shapes.\n"
    if objs:
        obj = objs[0]
        out += f"Sample object: {obj.Name}\n"
        out += f"Parents: {[p.Name for p in obj.InList]}\n"
        # Check if they are in a group
        for p in obj.InList:
            if p.isDerivedFrom("App::DocumentObjectGroup"):
                out += f"Group name (Layer): {p.Label}\n"

with open("c:/AI program/3D Modeling/mep-parser/_out.txt", "w") as f:
    f.write(out)
