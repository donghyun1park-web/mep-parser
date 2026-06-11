import FreeCAD as App

doc = App.ActiveDocument
walls = [obj for obj in doc.Objects if "Wall_" in obj.Label and "Closed" not in obj.Label]
out = ""
if walls:
    w = walls[0]
    out += f"Wall 0 Label: {w.Label}\n"
    out += f"Base object: {w.Base.Name if w.Base else 'None'}\n"
    if w.Base and hasattr(w.Base, 'Shape'):
        v = w.Base.Shape.Vertexes
        if v:
            out += f"Base P1: {v[0].Point.x}, {v[0].Point.y}\n"
            out += f"Base P2: {v[-1].Point.x}, {v[-1].Point.y}\n"
else:
    out += "No Wall_ objects found\n"

# DXF에서 가져온 2D 선들의 좌표도 하나 확인
dxf_lines = [obj for obj in doc.Objects if hasattr(obj, 'Shape') and "Wall" not in obj.Name]
if dxf_lines:
    v = dxf_lines[0].Shape.Vertexes
    if v:
        out += f"\nDXF Line 0 P1: {v[0].Point.x}, {v[0].Point.y}\n"
    
with open("c:/AI program/3D Modeling/mep-parser/_out.txt", "w") as f:
    f.write(out)
