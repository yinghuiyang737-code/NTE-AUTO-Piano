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

## 自动更新与发布

程序启动后会通过 GitHub Releases 检查最新正式版，也可点击“检查更新”手动检查。
发布新版本时，在本仓库创建版本号高于当前版本的 Release（例如 v6.5.0），
并上传形如 `NTE_AUTO_Piano_1.4_.exe` 的 .exe。旧版检测到后会提示下载并运行安装程序，
安装程序会覆盖旧版程序，同时保留用户位于本地应用数据目录中的曲谱库。

## GitHub 云谱库

程序会把 GitHub 仓库的 `scores/` 文件夹当成共享曲谱库。

- 点击“下载云谱”：读取 `scores/` 中的 `.mid`、`.midi`、`.txt`、`.zip`，下载后自动导入本地曲谱库。
- 点击“上传云谱”：选择本地曲谱或 ZIP 曲谱包，首次会要求填写 GitHub Token，之后会上传到 `scores/文件名`。
- 上传同名文件会替换 GitHub 上的旧文件。
- Token 只保存在本机 `%LOCALAPPDATA%\NTEAutoPiano\settings.json`，不会写入源码、安装包或 ZIP。

Token 权限建议：仓库是私有库用 `repo`，公开库细分权限可给 Contents read/write。
## ZIP 音画同步配置

ZIP 中可以额外放入一个名为 config.json 的文件。视频始终按原始速度播放，
程序会根据 sync_nodes 将 MIDI 音乐分段自动变速，使每个音乐时间与指定视频时间重合。
时间单位为秒，第一个节点必须为 0、0，之后所有 music 与 video 值必须严格递增。

示例：

    {
      "sync_nodes": [
        {"music": 0, "video": 0},
        {"music": 30.0, "video": 29.5},
        {"music": 60.0, "video": 61.2}
      ]
    }

启用同步节点后，界面的手动速度滑块不参与播放速度计算。
## 1.3 更新说明

- 启动时自动申请 Windows 管理员权限，提升游戏内按键注入兼容性。
- 自动更新会扫描仓库内最多 100 个正式 Releases，并同时检查附件文件名。
- 即使多个版本上传在同一个 Release 中，也会选择数字版本最高的 NTE_AUTO_Piano_x.x_.exe。
## 1.3.1 同步与性能修复

- 等待视频首帧完成后再启动音乐时钟，消除悬浮窗创建造成的整体偏移。
- 同一时间的和弦改为同时按下，降低密集 MIDI 的累计阻塞。
- 琴键触发保持时间由 45ms 优化为 18ms。
- 视频预览最多刷新 30 帧，但继续按视频原始时间轴跳帧纠偏，不改变视频速度。
- 使用 OpenCV 先缩放画面，减少界面线程的高质量重复缩放开销。