import ctypes
from pathlib import Path
import time
import tkinter as tk

import cv2
from PIL import Image, ImageSequence, ImageTk


class MediaOverlay:
    def __init__(self, root):
        self.root=root; self.window=None; self.label=None; self.job=None
        self.capture=None; self.frames=None; self.frame_index=0; self.photo=None
        self.paused=False; self.speed_getter=lambda:1.0; self.song_duration=0
        self.video_fps=30.0; self.video_duration=0; self.video_source_time=0
        self.last_clock=None

    def close(self):
        if self.job:
            try:self.root.after_cancel(self.job)
            except Exception:pass
        self.job=None
        if self.capture:self.capture.release()
        self.capture=None;self.frames=None;self.photo=None;self.paused=False
        if self.window:
            try:self.window.destroy()
            except Exception:pass
        self.window=None;self.label=None;self.last_clock=None

    def pause(self):self.paused=True
    def resume(self):
        self.paused=False;self.last_clock=time.perf_counter()

    def _prepare_window(self,width,height):
        self.close();win=tk.Toplevel(self.root);win.overrideredirect(True)
        win.attributes("-topmost",True);win.configure(bg="black")
        x=max(0,win.winfo_screenwidth()-width-28);win.geometry(f"{width}x{height}+{x}+28")
        label=tk.Label(win,bg="black",bd=0,highlightthickness=0);label.pack(fill="both",expand=True)
        win.update_idletasks();hwnd=win.winfo_id()
        exstyle=ctypes.windll.user32.GetWindowLongW(hwnd,-20)
        ctypes.windll.user32.SetWindowLongW(hwnd,-20,exstyle|0x00080000|0x00000020|0x00000080)
        ctypes.windll.user32.SetLayeredWindowAttributes(hwnd,0,255,0x2)
        self.window,self.label=win,label

    @staticmethod
    def _fit_size(width,height):
        scale=min(1.0,720/max(1,width),480/max(1,height))
        return max(1,int(width*scale)),max(1,int(height*scale))

    def _speed(self):
        try:return max(.1,float(self.speed_getter()))
        except Exception:return 1.0

    def start(self,path,song_duration=0,speed_getter=None):
        self.close()
        if not path or not Path(path).exists():return
        self.song_duration=max(.01,float(song_duration or 0))
        self.speed_getter=speed_getter or (lambda:1.0)
        if Path(path).suffix.lower()==".gif":self._start_gif(path)
        else:self._start_video(path)

    def _start_gif(self,path):
        image=Image.open(path);width,height=self._fit_size(*image.size);self._prepare_window(width,height)
        self.frames=[]
        for frame in ImageSequence.Iterator(image):
            duration=max(20,int(frame.info.get("duration",80)))
            resized=frame.convert("RGBA").resize((width,height),Image.Resampling.LANCZOS)
            self.frames.append((resized.copy(),duration))
        self.frame_index=0;self._show_gif_frame()

    def _show_gif_frame(self):
        if not self.window or not self.frames:return
        if self.paused:
            self.job=self.root.after(40,self._show_gif_frame);return
        frame,duration=self.frames[self.frame_index];self.photo=ImageTk.PhotoImage(frame)
        self.label.configure(image=self.photo);self.frame_index=(self.frame_index+1)%len(self.frames)
        self.job=self.root.after(duration,self._show_gif_frame)

    def _start_video(self,path):
        capture=cv2.VideoCapture(str(path))
        if not capture.isOpened():return
        raw_w=int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)) or 640
        raw_h=int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 360
        width,height=self._fit_size(raw_w,raw_h);self._prepare_window(width,height)
        self.capture=capture;self.video_size=(width,height)
        self.video_fps=capture.get(cv2.CAP_PROP_FPS) or 30.0
        frame_count=capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        self.video_duration=frame_count/self.video_fps if frame_count>0 else self.song_duration
        self.video_source_time=0.0;self.last_clock=time.perf_counter();self._show_video_frame()

    def _show_video_frame(self):
        if not self.window or not self.capture:return
        now=time.perf_counter()
        if self.paused:
            self.last_clock=now;self.job=self.root.after(30,self._show_video_frame);return
        elapsed=max(0,now-(self.last_clock or now));self.last_clock=now
        # 始终按视频自身的原始时间轴播放；落后时跳帧纠偏，但绝不变速。
        self.video_source_time+=elapsed
        ended=self.video_duration>0 and self.video_source_time>=self.video_duration
        if ended:self.video_source_time=max(0,self.video_duration-(1/self.video_fps))
        desired=max(0,int(self.video_source_time*self.video_fps))
        current=int(self.capture.get(cv2.CAP_PROP_POS_FRAMES))
        if abs(desired-current)>1:self.capture.set(cv2.CAP_PROP_POS_FRAMES,desired)
        ok,frame=self.capture.read()
        if not ok:
            self.capture.set(cv2.CAP_PROP_POS_FRAMES,0);self.video_source_time=0;ok,frame=self.capture.read()
        if ok:
            frame=cv2.cvtColor(frame,cv2.COLOR_BGR2RGB)
            image=Image.fromarray(frame).resize(self.video_size,Image.Resampling.LANCZOS)
            self.photo=ImageTk.PhotoImage(image);self.label.configure(image=self.photo)
        if not ended:self.job=self.root.after(16,self._show_video_frame)
