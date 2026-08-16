import ctypes
import heapq
import struct
import threading
import time
import tkinter as tk
import mido
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from midi_converter_v5 import convert_to_21key, convert_to_36key
from import_store_v2 import ImportStore
from media_overlay_v2 import MediaOverlay

APP = "异环自动钢琴"
APP_VERSION = "6.3.0"
KEYS = "ZXCVBNMASDFGHJQWERTYU"
VK = {c: ord(c) for c in KEYS}


def send_key(ch, down=True):
    vk = {"SHIFT": 0x10, "CTRL": 0x11}.get(ch, VK.get(ch))
    if vk is None:
        return
    scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
    flags = 0x0008 | (0 if down else 0x0002)
    ctypes.windll.user32.keybd_event(0, scan, flags, 0)


def tap(spec, hold=.045):
    parts = spec.split("+")
    modifier = parts[0] if len(parts) == 2 else None
    key = parts[-1]
    if modifier: send_key(modifier, True)
    send_key(key, True)
    time.sleep(hold)
    send_key(key, False)
    if modifier: send_key(modifier, False)

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
        self.configure(bg="#10131a"); self.events=[]; self.path=None; self.media_path=None
        self.store=ImportStore(); self.overlay=MediaOverlay(self)
        self.worker=None; self.stop_evt=threading.Event(); self.pause_evt=threading.Event()
        self.speed=tk.DoubleVar(value=1.0); self.status=tk.StringVar(value="请先导入曲谱")
        self._ui(); self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(80, self.emergency_poll)
        self.after(180, self.refresh_library)
        self.after(250, self.restore_last)

    def _ui(self):
        style=ttk.Style(self); style.theme_use("clam")
        style.configure("TFrame", background="#10131a")
        style.configure("TLabel", background="#10131a", foreground="#dce5f5", font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 22, "bold"), foreground="#f2f6ff")
        style.configure("TButton", font=("Microsoft YaHei UI", 11), padding=(16,10))
        box=ttk.Frame(self, padding=28); box.pack(fill="both", expand=True)
        ttk.Label(box,text="异环自动钢琴",style="Title.TLabel").pack(anchor="w")
        ttk.Label(box,text="开源版 v6.3 · 21 / 36 键 · MIDI / ZIP · F12 紧急停止",foreground="#8290a8").pack(anchor="w",pady=(4,24))
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
        speedrow=ttk.Frame(box); speedrow.pack(fill="x",pady=(8,4))
        ttk.Label(speedrow,text="速度").pack(side="left")
        ttk.Scale(speedrow,from_=.25,to=2.0,variable=self.speed,command=lambda _:self.speed_label.config(text=f"{self.speed.get():.2f}×")).pack(side="left",fill="x",expand=True,padx=14)
        self.speed_label=ttk.Label(speedrow,text="1.00×",width=7); self.speed_label.pack(side="right")
        ttk.Separator(box).pack(fill="x",pady=22)
        ttk.Label(box,textvariable=self.status,font=("Microsoft YaHei UI",11,"bold")).pack(anchor="w")
        self.progress=ttk.Progressbar(box,mode="determinate"); self.progress.pack(fill="x",pady=(12,10))
        ttk.Label(box,text="文本格式：@120  1 2 3 [135] - 5 ｜ 默认数字为中音，也可直接写 Q–U / A–J / Z–M",foreground="#8290a8").pack(anchor="w")

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
            self.set_loaded(item["midi"],item.get("media"),item.get("display_name") or Path(item["midi"]).name,False)
            self.status.set(f"已从本地曲谱库载入：{item.get('display_name')}")
        except Exception as e:messagebox.showerror("曲谱库载入失败",str(e))
    def restore_last(self):
        data=self.store.load()
        if not data:return
        try:
            self.set_loaded(data["midi"],data.get("media"),data.get("display_name") or Path(data["midi"]).name,False)
            self.status.set(f"已自动恢复上次曲谱：{data.get('display_name') or Path(data['midi']).name}")
        except Exception:
            pass

    def set_loaded(self,midi,media,display_name,notify=True):
        self.mode=detect_midi_mode(midi)
        self.events=parse_midi(midi); self.path=midi; self.media_path=media
        media_text=" · 含动图/视频" if media else ""
        self.file_label.config(text=f"{display_name} · {self.mode}键谱{media_text} · {len(self.events)} 个播放事件")
        self.progress["maximum"]=max(1,len(self.events)); self.progress["value"]=0
        self.status.set(f"已识别为 {self.mode} 键谱；点击开始后请切回对应游戏界面")
        self.store.save(midi,media,display_name,self.mode)
        if notify:
            extra="\n播放时将自动显示 ZIP 内的媒体悬浮窗。" if media else ""
            messagebox.showinfo("键位检测",f"检测到这是 {self.mode} 键谱。\n请确认游戏当前打开的是 {self.mode} 键钢琴界面。{extra}")

    def load(self):
        p=filedialog.askopenfilename(title="选择曲谱",filetypes=[("曲谱或曲谱包","*.mid *.midi *.txt *.zip"),("MIDI","*.mid *.midi"),("ZIP 曲谱包","*.zip"),("所有文件","*.*")])
        if not p:return
        try:
            if Path(p).suffix.lower()==".txt":
                self.mode=21; self.events=parse_text(p); self.path=p; self.media_path=None
                self.file_label.config(text=f"{Path(p).name} · 21键文本谱 · {len(self.events)} 个播放事件")
                self.status.set("已导入 21 键文本谱")
                self.progress["maximum"]=max(1,len(self.events)); self.progress["value"]=0
            else:
                midi,media,display_name=self.store.import_file(p)
                self.set_loaded(midi,media,display_name,True)
        except Exception as e:
            messagebox.showerror("无法导入",str(e))
    def start(self):
        if not self.events: messagebox.showinfo("提示","请先导入曲谱"); return
        if self.worker and self.worker.is_alive(): self.pause_evt.clear(); self.status.set("正在播放"); return
        self.stop_evt.clear(); self.pause_evt.clear(); self.worker=threading.Thread(target=self.play,daemon=True); self.worker.start()

    def play(self):
        self.status.set("3 秒后开始，请切回游戏窗口…")
        for _ in range(30):
            if self.stop_evt.wait(.1): return
        if self.media_path: self.after(0,lambda:self.overlay.start(self.media_path,self.events[-1][0] if self.events else 0,lambda:self.speed.get()))
        origin=time.perf_counter(); paused=0.0; pause_start=None
        for i,(at,key) in enumerate(self.events):
            while self.pause_evt.is_set() and not self.stop_evt.is_set():
                if pause_start is None: pause_start=time.perf_counter()
                time.sleep(.05)
            if pause_start is not None: paused += time.perf_counter()-pause_start; pause_start=None
            target=origin+paused+at/max(.1,self.speed.get())
            while not self.stop_evt.is_set() and time.perf_counter()<target: time.sleep(.002)
            if self.stop_evt.is_set(): return
            tap(key); self.after(0,lambda n=i+1:self.progress.configure(value=n))
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


if __name__ == "__main__":
    App().mainloop()
