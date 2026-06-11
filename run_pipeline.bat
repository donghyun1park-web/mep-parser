@echo off
echo 1. Parsing original DXF into geometry...
python make_json.py

echo 2. Copying to temp_geometry.json...
copy /Y geometry.json temp_geometry.json

echo 3. Clearing old geometry in FreeCAD...
python fc_live_cli.py exec clear_doc.py

echo 4. Building new geometry in FreeCAD...
python fc_live_cli.py exec build_live_walls_2.py

echo Pipeline complete!
