import json
log_file = r"C:\Users\User\.gemini\antigravity\brain\6e4fe4a2-ce22-427c-ac50-faf852c0b2e6\.system_generated\logs\transcript.jsonl"
with open(log_file, "r", encoding="utf-8") as f:
    lines = f.readlines()
    
user_msgs = [json.loads(line)["content"] for line in lines if '"type":"USER_INPUT"' in line]
msg = user_msgs[-2]  # the 4th one
with open(r"c:\AI program\3D Modeling\mep-parser\debug_tools\msg4.txt", "w", encoding="utf-8") as f:
    f.write(msg)
