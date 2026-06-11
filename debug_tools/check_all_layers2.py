import ezdxf

doc = ezdxf.readfile("temp_export_fixed.dxf")
msp = doc.modelspace()

layers = set()
for e in msp:
    layers.add(e.dxf.layer)

print("Layers in temp_export_fixed:")
print(sorted(list(layers)))
