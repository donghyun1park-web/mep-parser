import sys
import ezdxf

sys.path.insert(0, r"c:\AI program\3D Modeling\mep-parser")
from extractors import WallExtractor

dxf_path = r"c:\지하4층 건축평면도-1.dxf"
config_path = r"c:\AI program\3D Modeling\mep-parser\layers_config.yaml"

extractor = WallExtractor(config_path)
print("Config mappings loaded:", list(extractor.wall_mappings.keys()))

wall_data = extractor.extract_from_dxf(dxf_path)
print(f"Total extracted walls: {len(wall_data)}")
