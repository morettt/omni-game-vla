# omni-game-vla

Windows 游戏录制器。每 500 ms 截图一次，保存为 224 × 224 PNG。录制期间，鼠标左键点击会记录到该点击之前最近的一张截图，坐标使用原始屏幕坐标。

1. 安装带 Tkinter 的 Python 3，运行 `python -m pip install -r requirements.txt`。
2. 双击 `录制真实PVZ.bat`。按 Ctrl+K 开始或停止，按 Ctrl+Shift+K 退出。

录制文件写到 `真正PVZ/全新录制/`。
