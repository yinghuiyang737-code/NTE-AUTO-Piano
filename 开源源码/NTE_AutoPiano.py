import base64
import ctypes
import heapq
import json
import os
import re
import subprocess
import sys
import tempfile
import struct
import threading
import time
import tkinter as tk
import urllib.parse
import urllib.request
import mido
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from midi_converter_v5 import convert_to_21key, convert_to_36key
from import_store_v2 import ImportStore
from media_overlay_v2 import MediaOverlay

APP = "异环自动钢琴"
APP_VERSION = "1.4"
GITHUB_REPO = "yinghuiyang737-code/NTE-AUTO-Piano"
RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases?per_page=100"
SCORES_API = f"https://api.github.com/repos/{GITHUB_REPO}/contents/scores"
CLOUD_SCORE_EXTS = {".mid", ".midi", ".zip", ".txt"}
KEYS = "ZXCVBNMASDFGHJQWERTYU"
VK = {c: ord(c) for c in KEYS}


def send_key(ch, down=True):
    vk = {"SHIFT": 0x10, "CTRL": 0x11}.get(ch, VK.get(ch))
    if vk is None:
        return
    scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
    flags = 0x0008 | (0 if down else 0x0002)
    ctypes.windll.user32.keybd_event(0, scan, flags, 0)


def tap_many(specs, hold=.014):
    groups={None:[], "SHIFT":[], "CTRL":[]}
    for spec in dict.fromkeys(specs):
        parts=spec.split("+");modifier=parts[0] if len(parts)==2 else None
        groups.setdefault(modifier,[]).append(parts[-1])
    # 不同修饰键组绝不重叠，避免意外组成 Ctrl+Shift+S 等系统快捷键。
    for modifier in (None,"SHIFT","CTRL"):
        keys=groups.get(modifier) or []
        if not keys:continue
        if modifier:send_key(modifier,True)
        for key in keys:send_key(key,True)
        time.sleep(hold)
        for key in reversed(keys):send_key(key,False)
        if modifier:send_key(modifier,False)

def vlq(data, pos):
    value = 0
    while True:
        b = data[pos]; pos += 1
        value = (value << 7) | (b & 127)
        if not b & 128:
            return value, pos


NATURAL_NOTES = (48,50,52,53,55,57,59,60,62,64,65,67,69,71,72,74,76,77,79,81,83)
MAP_21 = dict(zip(NATURAL_NOTES, KEYS))
MAP_36 = {}
for base, row in ((48,"ZXCVBNM"),(60,"ASDFGHJ"),(72,"QWERTYU")):
    specs=(row[0],f"SHIFT+{row[0]}",row[1],f"CTRL+{row[2]}",row[2],row[3],f"SHIFT+{row[3]}",row[4],f"SHIFT+{row[4]}",row[5],f"CTRL+{row[6]}",row[6])
    MAP_36.update({base+i:spec for i,spec in enumerate(specs)})


def detect_midi_mode(path):
    name=Path(path).stem.lower().replace(" ","")
    if "36键" in name or "36key" in name: return 36
    if "21键" in name or "21key" in name: return 21
    mid=mido.MidiFile(path, clip=True)
    for track in mid.tracks:
        for msg in track:
            if msg.type=="note_on" and msg.velocity>0 and 48<=msg.note<=83 and msg.note not in NATURAL_NOTES:
                return 36
    return 21


def parse_midi(path):
    mode=detect_midi_mode(path)
    key_by_note=MAP_36 if mode==36 else MAP_21
    mid=mido.MidiFile(path, clip=True); now=0.0; active={}; events=[]; repeat_interval=.14
    for msg in mid:
        now+=msg.time
        if msg.type=="note_on" and msg.velocity>0 and msg.note in key_by_note:
            ident=(msg.channel,msg.note); active.setdefault(ident,[]).append(now)
            events.append((now,key_by_note[msg.note]))
        elif msg.type in ("note_off","note_on") and (msg.type=="note_off" or msg.velocity==0):
            ident=(msg.channel,msg.note); starts=active.get(ident)
            if starts and msg.note in key_by_note:
                start=starts.pop(0); repeat_at=start+repeat_interval
                while repeat_at<now-.03:
                    events.append((repeat_at,key_by_note[msg.note])); repeat_at+=repeat_interval
    events.sort(key=lambda item:item[0])
    if not events: raise ValueError(f"没有找到可播放的 {mode} 键音符")
    return events

NOTE_MAP = {}
for octave, row in ((3, "ZXCVBNM"), (4, "ASDFGHJ"), (5, "QWERTYU")):
    for degree, key in enumerate(row, 1): NOTE_MAP[f"{degree}{octave}"] = key
NOTE_MAP.update({str(i): k for i, k in enumerate("ASDFGHJ", 1)})


def parse_text(path):
    # 每个空格为一拍；[135] 为和弦；- 为休止；@120 设置 BPM。
    text = Path(path).read_text(encoding="utf-8-sig")
    bpm, beat, events = 120, 0, []
    for token in text.replace("\n", " ").split():
        if token.startswith("@"):
            bpm = max(20, min(400, int(token[1:]))); continue
        when = beat * 60 / bpm
        if token != "-":
            notes = token[1:-1] if token.startswith("[") and token.endswith("]") else token
            parts = notes.split(",") if "," in notes else list(notes)
            for note in parts:
                note = note.strip()
                if note in NOTE_MAP: events.append((when, NOTE_MAP[note]))
                elif note.upper() in KEYS: events.append((when, note.upper()))
        beat += 1
    if not events: raise ValueError("曲谱中没有可播放音符")
    return events


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP); self.geometry("930x575"); self.minsize(860, 535)
        self.configure(bg="#10131a"); self.events=[]; self.path=None; self.media_path=None; self.config_path=None; self.sync_nodes=[]
        self.store=ImportStore(); self.overlay=MediaOverlay(self)
        self.worker=None; self.stop_evt=threading.Event(); self.pause_evt=threading.Event(); self.media_ready_evt=threading.Event()
        self.speed=tk.DoubleVar(value=1.0); self.status=tk.StringVar(value="请先导入曲谱")
        self._ui(); self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(80, self.emergency_poll)
        self.after(180, self.refresh_library)
        self.after(250, self.restore_last)
        self.after(1800, self.check_update_silently)

    def _ui(self):
        style=ttk.Style(self); style.theme_use("clam")
        style.configure("TFrame", background="#10131a")
        style.configure("TLabel", background="#10131a", foreground="#dce5f5", font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 22, "bold"), foreground="#f2f6ff")
        style.configure("TButton", font=("Microsoft YaHei UI", 11), padding=(16,10))
        box=ttk.Frame(self, padding=28); box.pack(fill="both", expand=True)
        ttk.Label(box,text="异环自动钢琴",style="Title.TLabel").pack(anchor="w")
        ttk.Label(box,text=f"正式版 {APP_VERSION} · 21 / 36 键 · MIDI / ZIP · F12 紧急停止",foreground="#8290a8").pack(anchor="w",pady=(4,24))
        self.file_label=ttk.Label(box,text="尚未选择曲谱",padding=(12,14),background="#1b2230")
        self.file_label.pack(fill="x")
        librow=ttk.Frame(box); librow.pack(fill="x",pady=(12,0))
        ttk.Label(librow,text="本地曲谱库").pack(side="left",padx=(0,10))
        self.library_var=tk.StringVar()
        self.library_combo=ttk.Combobox(librow,textvariable=self.library_var,state="readonly",font=("Microsoft YaHei UI",10))
        self.library_combo.pack(side="left",fill="x",expand=True)
        self.library_combo.bind("<<ComboboxSelected>>",lambda _e:self.load_from_library())
        ttk.Button(librow,text="使用选中曲谱",command=self.load_from_library).pack(side="left",padx=(10,0))
        self.library_entries=[]
        row=ttk.Frame(box); row.pack(fill="x",pady=18)
        ttk.Button(row,text="导入曲谱",command=self.load).pack(side="left",padx=(0,9))
        self.start_btn=ttk.Button(row,text="开始",command=self.start); self.start_btn.pack(side="left",padx=9)
        self.pause_btn=ttk.Button(row,text="暂停",command=self.pause); self.pause_btn.pack(side="left",padx=9)
        ttk.Button(row,text="重新开始",command=self.restart).pack(side="left",padx=9)
        ttk.Button(row,text="转换21键",command=self.convert_midi).pack(side="left",padx=9)
        ttk.Button(row,text="转换36键",command=self.convert_midi_36).pack(side="left",padx=9)
        ttk.Button(row,text="下载云谱",command=self.cloud_download_dialog).pack(side="left",padx=9)
        ttk.Button(row,text="上传云谱",command=self.cloud_upload_dialog).pack(side="left",padx=9)
        ttk.Button(row,text="检查更新",command=lambda:self.check_update(False)).pack(side="right",padx=(9,0))
        speedrow=ttk.Frame(box); speedrow.pack(fill="x",pady=(8,4))
        ttk.Label(speedrow,text="速度").pack(side="left")
        ttk.Scale(speedrow,from_=.25,to=2.0,variable=self.speed,command=lambda _:self.speed_label.config(text=f"{self.speed.get():.2f}×")).pack(side="left",fill="x",expand=True,padx=14)
        self.speed_label=ttk.Label(speedrow,text="1.00×",width=7); self.speed_label.pack(side="right")
        ttk.Separator(box).pack(fill="x",pady=22)
        ttk.Label(box,textvariable=self.status,font=("Microsoft YaHei UI",11,"bold")).pack(anchor="w")
        self.progress=ttk.Progressbar(box,mode="determinate"); self.progress.pack(fill="x",pady=(12,10))
        ttk.Label(box,text="文本格式：@120  1 2 3 [135] - 5 ｜ 默认数字为中音，也可直接写 Q–U / A–J / Z–M",foreground="#8290a8").pack(anchor="w")

    @staticmethod
    def version_tuple(value):
        match=re.search(r"(?<!\d)(\d+(?:\.\d+){1,2})(?!\d)",str(value))
        if not match:return None
        parts=[int(item) for item in match.group(1).split(".")]
        return tuple((parts+[0,0,0])[:3])

    @classmethod
    def release_version(cls,release):
        values=[release.get("tag_name",""),release.get("name","")]
        values.extend(asset.get("name","") for asset in release.get("assets") or [])
        versions=[parsed for parsed in (cls.version_tuple(value) for value in values) if parsed]
        if not versions:return None
        parsed=max(versions)
        display=f"{parsed[0]}.{parsed[1]}" if parsed[2]==0 else ".".join(map(str,parsed))
        return parsed,display

    def check_update_silently(self):
        self.check_update(True)

    def check_update(self, silent=True):
        threading.Thread(target=self._check_update_worker,args=(silent,),daemon=True).start()

    def _check_update_worker(self, silent):
        try:
            request=urllib.request.Request(RELEASES_API,headers={"Accept":"application/vnd.github+json","User-Agent":f"NTE-Auto-Piano/{APP_VERSION}"})
            with urllib.request.urlopen(request,timeout=8) as response:
                releases=json.loads(response.read().decode("utf-8"))
            candidates=[]
            for release in releases:
                if release.get("draft") or release.get("prerelease"):continue
                parsed=self.release_version(release)
                if parsed:candidates.append((parsed[0],parsed[1],release))
            if not candidates:raise RuntimeError("没有找到带数字版本号的正式版 Release")
            newest_tuple,newest,release=max(candidates,key=lambda item:item[0])
            if newest_tuple<=self.version_tuple(APP_VERSION):
                if not silent:self.after(0,lambda:messagebox.showinfo("检查更新",f"当前已是最新版 {APP_VERSION}。"))
                return
            assets=release.get("assets") or []
            installers=[a for a in assets if str(a.get("name","")).lower().endswith(".exe")]
            matching=[a for a in installers if self.version_tuple(a.get("name",""))==newest_tuple]
            if matching:asset=matching[0]
            elif installers:asset=installers[0]
            else:raise RuntimeError(f"版本 {newest} 尚未附带 Windows 安装包")
            self.after(0,lambda:self._offer_update(newest,release.get("body") or "",asset))
        except Exception as exc:
            if not silent:
                self.after(0,lambda e=str(exc):messagebox.showwarning("检查更新",f"暂时无法检查更新。\n\n{e}"))
    def _offer_update(self, newest, notes, asset):
        summary=notes.strip()[:500] or "请安装新版本以获得最新功能和修复。"
        if not messagebox.askyesno("发现新版本",f"发现 {newest}（当前 {APP_VERSION}）。\n\n{summary}\n\n是否立即下载并安装？"):
            return
        self.status.set(f"正在下载 {newest}…")
        threading.Thread(target=self._download_update,args=(newest,asset),daemon=True).start()

    def _download_update(self, newest, asset):
        try:
            filename=asset.get("name") or f"NTE_AutoPiano_{newest}_Setup.exe"
            destination=os.path.join(tempfile.gettempdir(),filename)
            request=urllib.request.Request(asset["browser_download_url"],headers={"User-Agent":f"NTE-Auto-Piano/{APP_VERSION}"})
            with urllib.request.urlopen(request,timeout=30) as response,open(destination,"wb") as output:
                while True:
                    chunk=response.read(1024*1024)
                    if not chunk:break
                    output.write(chunk)
            self.after(0,lambda:self._launch_update(destination,newest))
        except Exception as exc:
            self.after(0,lambda e=str(exc):messagebox.showerror("更新失败",f"安装包下载失败。\n\n{e}"))

    def _launch_update(self, installer, newest):
        if not Path(installer).is_file():
            messagebox.showerror("更新失败","下载的安装包不存在。")
            return
        self.status.set(f"即将安装 {newest}")
        subprocess.Popen([installer,"/SP-","/CLOSEAPPLICATIONS","/RESTARTAPPLICATIONS"])
        self.close()

    def settings_file(self):
        base=Path(os.environ.get("LOCALAPPDATA",Path.home()))/"NTEAutoPiano"
        base.mkdir(parents=True,exist_ok=True)
        return base/"settings.json"

    def load_settings(self):
        try:
            return json.loads(self.settings_file().read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save_settings(self,data):
        self.settings_file().write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")

    def github_headers(self,token=None):
        headers={"Accept":"application/vnd.github+json","User-Agent":f"NTE-Auto-Piano/{APP_VERSION}","X-GitHub-Api-Version":"2022-11-28"}
        if token:headers["Authorization"]=f"Bearer {token}"
        return headers

    def github_token(self):
        settings=self.load_settings();token=settings.get("github_token")
        if token:return token
        token=simpledialog.askstring("GitHub Token","首次上传需要 GitHub Token。\n权限给 repo 或 contents:write 即可；Token 只保存在本机。",show="*")
        if not token:return None
        settings["github_token"]=token.strip();self.save_settings(settings)
        return settings["github_token"]

    def cloud_download_dialog(self):
        self.status.set("正在读取 GitHub 云谱库…")
        threading.Thread(target=self._cloud_list_worker,daemon=True).start()

    def _cloud_list_worker(self):
        try:
            request=urllib.request.Request(SCORES_API,headers=self.github_headers())
            with urllib.request.urlopen(request,timeout=12) as response:
                items=json.loads(response.read().decode("utf-8"))
            scores=[item for item in items if item.get("type")=="file" and Path(item.get("name","")).suffix.lower() in CLOUD_SCORE_EXTS]
            scores.sort(key=lambda item:item.get("name","").lower())
            self.after(0,lambda:self._show_cloud_scores(scores))
        except Exception as exc:
            self.after(0,lambda e=str(exc):messagebox.showwarning("云谱库",f"读取云谱库失败。\n\n请确认 GitHub 仓库里有 scores 文件夹。\n\n{e}"))

    def _show_cloud_scores(self,scores):
        if not scores:
            messagebox.showinfo("云谱库","GitHub 的 scores 文件夹里还没有谱。")
            self.status.set("云谱库为空")
            return
        win=tk.Toplevel(self);win.title("下载云谱");win.geometry("560x420");win.transient(self);win.configure(bg="#10131a")
        frame=ttk.Frame(win,padding=18);frame.pack(fill="both",expand=True)
        ttk.Label(frame,text="GitHub 云谱库",style="Title.TLabel").pack(anchor="w",pady=(0,12))
        names=[item["name"] for item in scores]
        listbox=tk.Listbox(frame,height=12,font=("Microsoft YaHei UI",10),bg="#1b2230",fg="#dce5f5",selectbackground="#3b82f6",highlightthickness=0,relief="flat")
        listbox.pack(fill="both",expand=True)
        for name in names:listbox.insert("end",name)
        listbox.selection_set(0)
        def choose():
            selected=listbox.curselection()
            if not selected:return
            item=scores[selected[0]];win.destroy();self._download_cloud_score(item)
        ttk.Button(frame,text="下载并导入",command=choose).pack(anchor="e",pady=(14,0))
        listbox.bind("<Double-Button-1>",lambda _e:choose())
        self.status.set(f"云谱库已读取：{len(scores)} 个谱")

    def _download_cloud_score(self,item):
        self.status.set(f"正在下载云谱：{item.get('name')}")
        threading.Thread(target=self._download_cloud_worker,args=(item,),daemon=True).start()

    def _download_cloud_worker(self,item):
        try:
            url=item.get("download_url")
            if not url:raise RuntimeError("这个文件没有可下载地址")
            safe_name=Path(item.get("name") or "cloud_score.mid").name
            destination=Path(tempfile.gettempdir())/safe_name
            request=urllib.request.Request(url,headers=self.github_headers())
            with urllib.request.urlopen(request,timeout=30) as response,destination.open("wb") as output:
                while True:
                    chunk=response.read(1024*512)
                    if not chunk:break
                    output.write(chunk)
            midi,media,config,display_name=self.store.import_file(destination)
            self.after(0,lambda:self._finish_cloud_download(midi,media,config,display_name))
        except Exception as exc:
            self.after(0,lambda e=str(exc):messagebox.showerror("云谱下载失败",str(e)))

    def _finish_cloud_download(self,midi,media,config,display_name):
        self.set_loaded(midi,media,config,display_name,True)
        self.refresh_library()
        self.status.set(f"云谱已下载并导入：{display_name}")

    def cloud_upload_dialog(self):
        source=filedialog.askopenfilename(title="选择要上传到云谱库的谱",filetypes=[("曲谱或曲谱包","*.mid *.midi *.txt *.zip"),("所有文件","*.*")])
        if not source:return
        if Path(source).suffix.lower() not in CLOUD_SCORE_EXTS:
            messagebox.showwarning("上传云谱","只支持上传 mid、midi、txt 或 zip。")
            return
        token=self.github_token()
        if not token:return
        if not messagebox.askyesno("上传云谱",f"将上传到 GitHub：scores/{Path(source).name}\n\n同名文件会被替换，继续吗？"):
            return
        self.status.set(f"正在上传云谱：{Path(source).name}")
        threading.Thread(target=self._cloud_upload_worker,args=(source,token),daemon=True).start()

    def _cloud_upload_worker(self,source,token):
        try:
            source_path=Path(source);name=source_path.name
            api=f"{SCORES_API}/{urllib.parse.quote(name)}"
            sha=None
            try:
                request=urllib.request.Request(api,headers=self.github_headers(token))
                with urllib.request.urlopen(request,timeout=12) as response:
                    existing=json.loads(response.read().decode("utf-8"));sha=existing.get("sha")
            except Exception:
                sha=None
            content=base64.b64encode(source_path.read_bytes()).decode("ascii")
            payload={"message":f"Upload score {name}","content":content}
            if sha:payload["sha"]=sha
            data=json.dumps(payload,ensure_ascii=False).encode("utf-8")
            request=urllib.request.Request(api,data=data,method="PUT",headers={**self.github_headers(token),"Content-Type":"application/json"})
            with urllib.request.urlopen(request,timeout=45) as response:
                result=json.loads(response.read().decode("utf-8"))
            url=(result.get("content") or {}).get("html_url") or f"https://github.com/{GITHUB_REPO}/tree/main/scores"
            self.after(0,lambda:self._finish_cloud_upload(name,url))
        except Exception as exc:
            self.after(0,lambda e=str(exc):messagebox.showerror("云谱上传失败",f"上传失败。\n\n如果提示 401/403，请检查 Token 权限。\n\n{e}"))

    def _finish_cloud_upload(self,name,url):
        self.status.set(f"云谱已上传：{name}")
        messagebox.showinfo("上传完成",f"已上传到 GitHub 云谱库。\n\n{name}\n\n别人点击 下载云谱 后就能看到。")
    def convert_midi(self):
        source=filedialog.askopenfilename(title="选择要转换的 MIDI",filetypes=[("MIDI 曲谱","*.mid *.midi"),("所有文件","*.*")])
        if not source:return
        source_path=Path(source)
        destination=filedialog.asksaveasfilename(title="保存异环 21 键版",defaultextension=".mid",initialfile=f"{source_path.stem}_异环21键版.mid",filetypes=[("MIDI 曲谱","*.mid")])
        if not destination:return
        try:
            self.status.set("正在分析调性并转换…"); self.update_idletasks()
            info=convert_to_21key(source,destination)
            self.events=parse_midi(destination); self.path=destination
            self.file_label.config(text=f"{Path(destination).name}   ·   {len(self.events)} 个播放事件")
            self.progress["maximum"]=max(1,len(self.events)); self.progress["value"]=0
            direction="升" if info["shift"]>0 else "降" if info["shift"]<0 else "不移调"
            shift_text=f"{direction}{abs(info['shift'])} 半音" if info["shift"] else direction
            self.status.set(f"转换完成并已导入：主旋律 {info['melody']}，伴奏 {info['accompaniment']}，{shift_text}")
            messagebox.showinfo("转换完成",f"21 键版已保存并自动导入。\n\n主旋律：{info['melody']} 个音符\n伴奏：{info['accompaniment']} 个音符\n移调：{shift_text}")
        except Exception as e:
            self.status.set("转换失败"); messagebox.showerror("转换失败",str(e))
    def convert_midi_36(self):
        source=filedialog.askopenfilename(title="选择要转换的 MIDI",filetypes=[("MIDI 曲谱","*.mid *.midi"),("所有文件","*.*")])
        if not source:return
        source_path=Path(source)
        destination=filedialog.asksaveasfilename(title="保存异环 36 键版",defaultextension=".mid",initialfile=f"{source_path.stem}_异环36键版.mid",filetypes=[("MIDI 曲谱","*.mid")])
        if not destination:return
        try:
            self.status.set("正在转换为 36 键…"); self.update_idletasks()
            info=convert_to_36key(source,destination)
            self.events=parse_midi(destination); self.path=destination; self.mode=36
            self.file_label.config(text=f"{Path(destination).name} · 36键谱 · {len(self.events)} 个播放事件")
            self.progress["maximum"]=max(1,len(self.events)); self.progress["value"]=0
            self.status.set(f"36 键转换完成：主旋律 {info['melody']}，伴奏 {info['accompaniment']}")
            messagebox.showinfo("转换完成",f"已保存并导入 36 键谱。\n请在游戏中打开 36 键钢琴界面。")
        except Exception as e:
            self.status.set("转换失败"); messagebox.showerror("转换失败",str(e))
    def refresh_library(self):
        self.library_entries=self.store.list_entries()
        labels=[]
        for item in self.library_entries:
            mode=f"{item.get('mode')}键" if item.get("mode") else "待检测"
            media=" · 含媒体" if item.get("media") else ""
            labels.append(f"{item.get('display_name','未命名')} · {mode}{media}")
        self.library_combo["values"]=labels
        if labels and self.library_combo.current()<0:self.library_combo.current(0)
        if not labels:self.library_var.set("尚无已导入曲谱")

    def load_from_library(self):
        index=self.library_combo.current()
        if index<0 or index>=len(self.library_entries):return
        item=self.library_entries[index]
        try:
            self.set_loaded(item["midi"],item.get("media"),item.get("config"),item.get("display_name") or Path(item["midi"]).name,False)
            self.status.set(f"已从本地曲谱库载入：{item.get('display_name')}")
        except Exception as e:messagebox.showerror("曲谱库载入失败",str(e))
    def restore_last(self):
        data=self.store.load()
        if not data:return
        try:
            self.set_loaded(data["midi"],data.get("media"),data.get("config"),data.get("display_name") or Path(data["midi"]).name,False)
            self.status.set(f"已自动恢复上次曲谱：{data.get('display_name') or Path(data['midi']).name}")
        except Exception:
            pass

    def set_loaded(self,midi,media,config,display_name,notify=True):
        self.mode=detect_midi_mode(midi)
        self.events=parse_midi(midi); self.path=midi; self.media_path=media; self.config_path=config
        self.sync_nodes=self.load_sync_nodes(config)
        media_text=" · 含动图/视频" if media else ""
        sync_text=f" · {len(self.sync_nodes)}个同步节点" if self.sync_nodes else ""
        self.file_label.config(text=f"{display_name} · {self.mode}键谱{media_text}{sync_text} · {len(self.events)} 个播放事件")
        self.progress["maximum"]=max(1,len(self.events)); self.progress["value"]=0
        self.status.set(f"已识别为 {self.mode} 键谱；点击开始后请切回对应游戏界面")
        self.store.save(midi,media,config,display_name,self.mode)
        if notify:
            extra="\n播放时将自动显示 ZIP 内的媒体悬浮窗。" if media else ""
            if self.sync_nodes:extra+=f"\n已载入 {len(self.sync_nodes)} 个音画同步节点，音乐将分段自动变速。"
            messagebox.showinfo("键位检测",f"检测到这是 {self.mode} 键谱。\n请确认游戏当前打开的是 {self.mode} 键钢琴界面。{extra}")

    def load_sync_nodes(self,config):
        if not config:return []
        try:
            data=json.loads(Path(config).read_text(encoding="utf-8-sig"))
            raw=data.get("sync_nodes",[])
            nodes=[(float(item["music"]),float(item["video"])) for item in raw]
            if len(nodes)<2:raise ValueError("sync_nodes 至少需要两个节点")
            if abs(nodes[0][0])>.001 or abs(nodes[0][1])>.001:
                raise ValueError("第一个同步节点必须是 music=0、video=0")
            for previous,current in zip(nodes,nodes[1:]):
                if current[0]<=previous[0] or current[1]<=previous[1]:
                    raise ValueError("同步节点的 music 和 video 时间必须严格递增")
            return nodes
        except Exception as exc:
            raise ValueError(f"config.json 格式错误：{exc}") from exc

    def playback_time(self,music_time):
        if not self.sync_nodes:return music_time/max(.1,self.speed.get())
        nodes=self.sync_nodes
        left,right=nodes[-2],nodes[-1]
        for index in range(1,len(nodes)):
            if music_time<=nodes[index][0]:
                left,right=nodes[index-1],nodes[index];break
        ratio=(music_time-left[0])/(right[0]-left[0])
        return max(0.0,left[1]+ratio*(right[1]-left[1]))
    def load(self):
        p=filedialog.askopenfilename(title="选择曲谱",filetypes=[("曲谱或曲谱包","*.mid *.midi *.txt *.zip"),("MIDI","*.mid *.midi"),("ZIP 曲谱包","*.zip"),("所有文件","*.*")])
        if not p:return
        try:
            if Path(p).suffix.lower()==".txt":
                self.mode=21; self.events=parse_text(p); self.path=p; self.media_path=None; self.config_path=None; self.sync_nodes=[]
                self.file_label.config(text=f"{Path(p).name} · 21键文本谱 · {len(self.events)} 个播放事件")
                self.status.set("已导入 21 键文本谱")
                self.progress["maximum"]=max(1,len(self.events)); self.progress["value"]=0
            else:
                midi,media,config,display_name=self.store.import_file(p)
                self.set_loaded(midi,media,config,display_name,True)
        except Exception as e:
            messagebox.showerror("无法导入",str(e))
    def start(self):
        if not self.events: messagebox.showinfo("提示","请先导入曲谱"); return
        if self.worker and self.worker.is_alive(): self.pause_evt.clear(); self.status.set("正在播放"); return
        self.stop_evt.clear(); self.pause_evt.clear(); self.worker=threading.Thread(target=self.play,daemon=True); self.worker.start()

    def _start_overlay_ready(self):
        try:
            self.overlay.start(self.media_path,self.events[-1][0] if self.events else 0,lambda:self.speed.get())
            self.overlay.resume()
        finally:
            self.media_ready_evt.set()

    def play(self):
        self.status.set("3 秒后开始，请切回游戏窗口…")
        for _ in range(30):
            if self.stop_evt.wait(.1):return
        if self.media_path:
            self.media_ready_evt.clear()
            self.after(0,self._start_overlay_ready)
            deadline=time.perf_counter()+8
            while not self.media_ready_evt.wait(.02):
                if self.stop_evt.is_set():return
                if time.perf_counter()>=deadline:break
        origin=time.perf_counter();paused=0.0;pause_start=None;i=0
        while i<len(self.events):
            at=self.events[i][0];specs=[];last_index=i
            while last_index<len(self.events) and abs(self.events[last_index][0]-at)<.003:
                specs.append(self.events[last_index][1]);last_index+=1
            while self.pause_evt.is_set() and not self.stop_evt.is_set():
                if pause_start is None:pause_start=time.perf_counter()
                time.sleep(.02)
            if pause_start is not None:
                paused+=time.perf_counter()-pause_start;pause_start=None
            target=origin+paused+self.playback_time(at)
            remaining=target-time.perf_counter()
            while not self.stop_evt.is_set() and remaining>0:
                time.sleep(min(.002,remaining));remaining=target-time.perf_counter()
            if self.stop_evt.is_set():return
            tap_many(specs)
            i=last_index
            self.after(0,lambda n=i:self.progress.configure(value=n))
        self.after(0,self.overlay.close)
        self.status.set("播放完成")
    def pause(self):
        if not self.worker or not self.worker.is_alive(): return
        if self.pause_evt.is_set():
            self.pause_evt.clear(); self.overlay.resume(); self.status.set("正在播放")
        else:
            self.pause_evt.set(); self.overlay.pause(); self.status.set("已暂停")

    def restart(self):
        self.stop_evt.set(); self.pause_evt.clear(); self.overlay.close(); self.progress["value"]=0
        self.after(60,self.restart_when_stopped)

    def restart_when_stopped(self):
        if self.worker and self.worker.is_alive():
            self.after(60,self.restart_when_stopped)
        else:
            self.start()
    def emergency_poll(self):
        if ctypes.windll.user32.GetAsyncKeyState(0x7B) & 1:
            self.stop_evt.set(); self.pause_evt.clear(); self.overlay.close(); self.status.set("已紧急停止（F12）")
        self.after(80,self.emergency_poll)

    def close(self): self.stop_evt.set(); self.overlay.close(); self.destroy()


def ensure_admin():
    try:
        if ctypes.windll.shell32.IsUserAnAdmin():return True
        arguments=sys.argv[1:] if getattr(sys,"frozen",False) else sys.argv
        result=ctypes.windll.shell32.ShellExecuteW(None,"runas",sys.executable,subprocess.list2cmdline(arguments),None,1)
        return result>32
    except Exception:
        return False


if __name__ == "__main__":
    if ensure_admin():
        App().mainloop()