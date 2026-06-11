import FreeCAD as App
import Part
import json

doc = App.ActiveDocument
data = []

for layer_obj in doc.Objects:
    if layer_obj.TypeId == "App::FeaturePython" and layer_obj.Name.startswith("Layer"):
        layer_name = layer_obj.Label
        children = []
        if hasattr(layer_obj, "Group"):
            children = layer_obj.Group
        elif hasattr(layer_obj, "OutList"):
            children = layer_obj.OutList
            
        for obj in children:
            if not hasattr(obj, "Shape"):
                continue
            shape = obj.Shape
            for edge in shape.Edges:
                try:
                    if type(edge.Curve).__name__ == "Line":
                        p1, p2 = edge.Vertexes[0].Point, edge.Vertexes[-1].Point
                        data.append({
                            "layer": layer_name,
                            "type": "Line",
                            "p1": [p1.x, p1.y, p1.z],
                            "p2": [p2.x, p2.y, p2.z]
                        })
                    elif type(edge.Curve).__name__ == "Circle":
                        # Arc or Circle
                        c = edge.Curve.Center
                        r = edge.Curve.Radius
                        data.append({
                            "layer": layer_name,
                            "type": "Circle",
                            "center": [c.x, c.y, c.z],
                            "radius": r
                        })
                except Exception:
                    pass

with open("c:/AI program/3D Modeling/mep-parser/fc_lines_dump.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False)

with open("c:/AI program/3D Modeling/mep-parser/_out.txt", "w", encoding="utf-8") as f:
    f.write(f"Dumped {len(data)} edges to JSON.\n")
