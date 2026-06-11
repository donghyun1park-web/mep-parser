import ezdxf

doc = ezdxf.readfile("c:/AI program/3D Modeling/mep-parser/temp_export.dxf")
msp = doc.modelspace()

layer_names = set()
for e in msp:
    layer_names.add(e.dxf.layer)

out = "Original layers:\n" + "\n".join(sorted(list(layer_names)))
with open("c:/AI program/3D Modeling/mep-parser/_out.txt", "w", encoding="utf-8", errors="replace") as f:
    f.write(out)
