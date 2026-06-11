import sys
sys.path.append("c:/AI program/3D Modeling/mep-parser")
import importlib
import freecad_live_addon
importlib.reload(freecad_live_addon)

out = "Addon reloaded successfully\n"
with open("c:/AI program/3D Modeling/mep-parser/_out.txt", "w") as f:
    f.write(out)
