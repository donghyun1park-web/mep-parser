import FreeCAD as App
import FreeCADGui as Gui

try:
    App.closeDocument("Test")
except Exception:
    pass

try:
    App.setActiveDocument("Unnamed")
    Gui.ActiveDocument = Gui.getDocument("Unnamed")
except Exception:
    pass

doc = App.ActiveDocument
with open("c:/AI program/3D Modeling/mep-parser/_out.txt", "w") as f:
    f.write(f"Active document is now {doc.Name if doc else 'None'}\n")
