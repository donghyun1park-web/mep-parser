# PySide compatibility helper (FreeCAD 0.20+ / 1.0+)
try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:
    try:
        from PySide2 import QtCore, QtGui, QtWidgets
    except ImportError:
        from PySide import QtCore, QtGui
        QtWidgets = QtGui

class LocalCLIMacro(QtWidgets.QDockWidget):
    def __init__(self):
        super().__init__("Antigravity Command Line")
        self.setAllowedAreas(QtCore.Qt.BottomDockWidgetArea | QtCore.Qt.RightDockWidgetArea)
        
        self.widget = QtWidgets.QWidget()
        self.layout = QtWidgets.QVBoxLayout()
        self.widget.setLayout(self.layout)
        
        self.output_log = QtWidgets.QTextEdit()
        self.output_log.setReadOnly(True)
        # Styled with dark theme for comfortable logging
        self.output_log.setStyleSheet("background-color: #1e1e1e; color: #00ff00; font-family: Consolas, monospace;")
        self.layout.addWidget(self.output_log)
        
        self.input_line = QtWidgets.QLineEdit()
        self.input_line.setPlaceholderText("명령어를 입력하세요 (예: 합치기 623 465, 높이 3000 전체, 두께 200 623)")
        self.input_line.setStyleSheet("font-size: 14px; padding: 5px;")
        self.input_line.returnPressed.connect(self.on_enter)
        self.layout.addWidget(self.input_line)
        
        self.setWidget(self.widget)
        self.pending_build = None
        self.log("⚡ CLI 준비 완료. '도움말'을 입력해보세요.")

    def log(self, text):
        self.output_log.append(text)
        self.output_log.verticalScrollBar().setValue(self.output_log.verticalScrollBar().maximum())

    def on_enter(self):
        cmd_text = self.input_line.text().strip()
        if not cmd_text:
            return
        
        self.input_line.clear()
        self.log(f"\n> {cmd_text}")
        
        try:
            import FreeCAD as App
            import FreeCADGui as Gui
            from .commands import process_cli_command
            process_cli_command(self, cmd_text)
            if App.ActiveDocument:
                App.ActiveDocument.recompute()
                Gui.updateGui()
        except Exception as e:
            self.log(f"[에러] {str(e)}")

def install_cli_macro():
    import FreeCADGui as Gui
    mw = Gui.getMainWindow()
    # Remove existing dock widget if present
    for dw in mw.findChildren(QtWidgets.QDockWidget):
        if dw.windowTitle() == "Antigravity Command Line":
            try:
                dw.close()
                mw.removeDockWidget(dw)
                dw.setParent(None)
                dw.deleteLater()
            except Exception:
                pass

    # Add new panel
    cli_macro = LocalCLIMacro()
    mw.addDockWidget(QtCore.Qt.BottomDockWidgetArea, cli_macro)
    cli_macro.show()
