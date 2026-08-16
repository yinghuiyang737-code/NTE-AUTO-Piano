# 异环自动钢琴

Windows 本地 MIDI 自动演奏工具，支持异环 21 键与 36 键钢琴、MIDI 自动转换、
ZIP 曲谱包、本地曲谱库，以及 GIF/视频置顶穿透悬浮窗。

## 源码文件

- `NTE_AutoPiano.py`：主界面、MIDI 解析与键盘播放。
- `midi_converter_v5.py`：21/36 键 MIDI 转换。
- `import_store_v2.py`：本地曲谱库、ZIP 导入与持久化。
- `media_overlay_v2.py`：GIF/视频悬浮窗。

## 本地运行

安装 Python 3.12 后执行：

```powershell
python -m pip install -r requirements.txt
python NTE_AutoPiano.py
```

## 构建 EXE

```powershell
python -m PyInstaller --clean --onefile --windowed --name "异环自动钢琴" --paths . NTE_AutoPiano.py
```

项目采用 MIT License。模拟输入功能可能受到游戏规则限制，请自行确认允许的使用场景。
