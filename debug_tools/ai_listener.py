import FreeCAD as App
import FreeCADGui as Gui
import os
import traceback

try:
    from PySide6 import QtCore
except ImportError:
    try:
        from PySide2 import QtCore
    except ImportError:
        from PySide import QtCore

# AI와 통신할 파일 경로
CMD_FILE = r"C:\AI program\3D Modeling\mep-parser\ai_cmd.py"
RES_FILE = r"C:\AI program\3D Modeling\mep-parser\ai_res.json"

def check_for_commands():
    if os.path.exists(CMD_FILE):
        try:
            with open(CMD_FILE, 'r', encoding='utf-8') as f:
                code = f.read()
            os.remove(CMD_FILE) # 실행 전 즉시 삭제
            
            # 코드 실행
            local_vars = {}
            exec(code, globals(), local_vars)
            
            # 결과 저장
            import json
            result_data = {"status": "ok", "result": local_vars.get('result', 'Success (No return value)')}
            with open(RES_FILE, 'w', encoding='utf-8') as f:
                json.dump(result_data, f, ensure_ascii=False)
                
        except Exception as e:
            import json
            error_data = {"error": str(e), "traceback": traceback.format_exc()}
            with open(RES_FILE, 'w', encoding='utf-8') as f:
                json.dump(error_data, f, ensure_ascii=False)

# 기존 타이머가 있다면 중지
if hasattr(App, '_ai_timer'):
    App._ai_timer.stop()

# 0.5초마다 파일 확인하는 타이머 시작
App._ai_timer = QtCore.QTimer()
App._ai_timer.timeout.connect(check_for_commands)
App._ai_timer.start(500)

print("✅ AI Copilot 연결 완료! 이제 AI가 내리는 명령을 실시간으로 수행합니다.")
