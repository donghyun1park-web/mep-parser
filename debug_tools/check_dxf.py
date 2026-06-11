import ezdxf

doc = ezdxf.readfile("c:/AI program/3D Modeling/mep-parser/temp_export.dxf")
msp = doc.modelspace()

inserts = msp.query('INSERT')
lines = msp.query('LINE')
polylines = msp.query('LWPOLYLINE')

out = f"Inserts: {len(inserts)}\n"
out += f"Lines: {len(lines)}\n"
out += f"Polylines: {len(polylines)}\n"

with open("c:/AI program/3D Modeling/mep-parser/_out.txt", "w") as f:
    f.write(out)
