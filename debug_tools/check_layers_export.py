import ezdxf
doc = ezdxf.readfile("temp_export.dxf")
layers = set()
for e in doc.modelspace():
    layers.add(e.dxf.layer)
with open("_out.txt", "w", encoding="utf-8", errors="replace") as f:
    f.write("\n".join(sorted(layers)))
