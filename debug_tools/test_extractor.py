import sys
import os

# Add mep-parser to sys.path
sys.path.insert(0, r"c:\AI program\3D Modeling\mep-parser")

from extractors import WallExtractor

# Create a dummy yaml config for test
import yaml
config = {
    "mappings": [
        {"layer": "A-WALL", "type": "Wall", "height": 3000, "default_thickness": 200, "label": "TestWall"}
    ]
}
config_path = r"c:\AI program\3D Modeling\mep-parser\debug_tools\test_config.yaml"
with open(config_path, "w", encoding="utf-8") as f:
    yaml.dump(config, f)

extractor = WallExtractor(config_path)

lines = [
    # A pair of parallel lines 200mm apart, length 1000
    (0, 0, 1000, 0),
    (0, 200, 1000, 200),
    
    # Another pair, length 500
    (2000, 500, 2500, 500),
    (2000, 600, 2500, 600)
]

# We need to test _extract_centerlines directly
props = config["mappings"][0]
results = extractor._extract_centerlines(lines, props)

for r in results:
    print(f"Centerline: {list(r['centerline'].coords)}, Thickness: {r['thickness']}, Height: {r['height']}, Label: {r['label']}")

