import json
import urllib.request
import sys

def main():
    try:
        print("Loading geometry.json...")
        with open("geometry.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        
        # We only want to build walls, so we can clear out other elements just to be safe, 
        # or leave them. The user specifically asked for walls.
        elements = data.get("elements", {})
        walls = elements.get("wall", [])
        print(f"Found {len(walls)} walls in geometry.json")
        
        payload = {"geometry": data}
        
        print("Sending to FreeCAD live server...")
        req = urllib.request.Request(
            "http://127.0.0.1:8081/build_geometry", 
            data=json.dumps(payload).encode('utf-8'), 
            headers={'Content-Type': 'application/json'}, 
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=120.0) as response:
            res_body = response.read().decode('utf-8')
            print("Response:", res_body)
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    main()
