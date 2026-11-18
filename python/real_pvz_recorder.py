"""500ms 224x224 desktop recording with pre-click screen-coordinate labels."""
from bisect import bisect_right
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
import traceback
from PIL import Image, ImageGrab
from recording_input import RecordingInput

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'真正PVZ'/'全新录制'


class RecordingStore:
    def __init__(self,folder):
        self.folder=Path(folder);self.images=self.folder/'images'
        self.images.mkdir(parents=True,exist_ok=True)
        self.path=self.folder/'data.json'
        self.rows=json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else []
        if not isinstance(self.rows,list):raise ValueError('已有录制 JSON 格式错误')
        self.next_index=max([0]+[int(p.stem) for p in self.images.glob('*.png') if p.stem.isdigit()])+1
        self.ends=[];self.indices=[];self.segment=round(time.time()*1000)

    def add_frame(self,image,completed,wall,bounds):
        name=f'images/{self.next_index:08d}.png';path=self.folder/name
        temporary=path.with_suffix('.png.tmp')
        image.convert('RGB').resize((224,224),Image.Resampling.BILINEAR).save(temporary,format='PNG',compress_level=1)
        temporary.replace(path)
        row={'图片':name,'时间戳':round(wall*1000),'录制段':self.segment,
             '屏幕区域':bounds,'点击':[]}
        self.rows.append(row);self.ends.append(completed);self.indices.append(len(self.rows)-1)
        self.next_index+=1

    def add_click(self,click):
        index=bisect_right(self.ends,click['perf'])-1
        if index<0:return False # No screenshot existed before this click.
        self.rows[self.indices[index]]['点击'].append({'横坐标':click['x'],'纵坐标':click['y'],'时间戳':round(click['wall']*1000)})
        return True

    def save(self):
        temporary=self.path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(self.rows,ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(self.path)


class CaptureWorker:
    def __init__(self,folder,inputs,messages,grab=None,bounds=None):
        self.folder=folder;self.inputs=inputs;self.messages=messages
        self.grab=grab or (lambda:ImageGrab.grab(all_screens=True))
        self.bounds=bounds or self.screen_bounds
        self.stop=threading.Event();self.thread=threading.Thread(target=self.run,daemon=True)

    @staticmethod
    def screen_bounds(image):
        user32=ctypes.windll.user32
        return {'左':user32.GetSystemMetrics(76),'上':user32.GetSystemMetrics(77),'宽':image.width,'高':image.height}

    def drain(self,store):
        while True:
            try:click=self.inputs.clicks.get_nowait()
            except queue.Empty:return
            store.add_click(click)

    def run(self):
        store=None
        try:
            store=RecordingStore(self.folder)
            while True:
                try:self.inputs.clicks.get_nowait()
                except queue.Empty:break
            self.inputs.active.set();deadline=time.perf_counter()
            while not self.stop.is_set():
                self.drain(store)
                image=self.grab();completed=time.perf_counter();wall=time.time()
                if self.stop.is_set():break
                store.add_frame(image,completed,wall,self.bounds(image))
                self.drain(store);store.save()
                deadline+=.5
                now=time.perf_counter()
                if now>deadline:deadline+=((now-deadline)//.5+1)*.5
                self.stop.wait(max(0,deadline-time.perf_counter()))
            self.inputs.active.clear();self.drain(store);store.save()
            self.messages.put(('stopped',len(store.ends)))
        except Exception:
            self.inputs.active.clear()
            if store is not None:
                try:self.drain(store);store.save()
                except Exception:pass
            self.messages.put(('error',traceback.format_exc()))


class RecorderBadge:
    def __init__(self,root,inputs=None):
        self.root=root;self.worker=None;self.messages=queue.Queue()
        root.overrideredirect(True);root.attributes('-topmost',True)
        root.configure(bg='#ff00ff');root.wm_attributes('-transparentcolor','#ff00ff')
        width,height=190,48
        root.geometry(f'{width}x{height}+{root.winfo_screenwidth()-width-20}+20')
        self.canvas=tk.Canvas(root,width=width,height=height,bg='#ff00ff',highlightthickness=0,borderwidth=0)
        self.canvas.pack()
        self.background=self.canvas.create_polygon(14,0,176,0,190,14,190,34,176,48,14,48,0,34,0,14,smooth=True,fill='#35413c',outline='')
        self.dot=self.canvas.create_oval(16,18,28,30,fill='#afbab1',outline='')
        self.label=self.canvas.create_text(108,24,text='录制结束',fill='white',font=('Microsoft YaHei',15,'bold'))
        root.update_idletasks()
        user32=ctypes.WinDLL('user32',use_last_error=True)
        user32.GetAncestor.argtypes=[wintypes.HWND,ctypes.c_uint];user32.GetAncestor.restype=wintypes.HWND
        hwnd=user32.GetAncestor(root.winfo_id(),2)
        user32.GetWindowLongW.argtypes=[wintypes.HWND,ctypes.c_int]
        user32.SetWindowLongW.argtypes=[wintypes.HWND,ctypes.c_int,ctypes.c_long]
        style=user32.GetWindowLongW(hwnd,-20)
        user32.SetWindowLongW(hwnd,-20,style|0x08000000|0x00000080|0x00000020|0x00080000)
        user32.SetWindowDisplayAffinity.argtypes=[wintypes.HWND,wintypes.DWORD]
        if not user32.SetWindowDisplayAffinity(hwnd,0x11):raise ctypes.WinError(ctypes.get_last_error())
        self.inputs=inputs or RecordingInput()
        root.protocol('WM_DELETE_WINDOW',self.close)
        root.after(20,self.poll)

    def state(self,text,recording=False):
        self.canvas.itemconfigure(self.label,text=text)
        self.canvas.itemconfigure(self.dot,fill='#ff655b' if recording else '#afbab1')
        self.canvas.itemconfigure(self.background,fill='#722d2d' if recording else '#35413c')

    def toggle(self):
        if self.worker is not None:
            self.inputs.active.clear();self.worker.stop.set();self.state('录制结束')
            return
        self.worker=CaptureWorker(DATA,self.inputs,self.messages)
        self.state('录制中',True);self.worker.thread.start()

    def poll(self):
        try:
            while True:
                command=self.inputs.hotkeys.get_nowait()
                if command=='exit':
                    self.close();return
                self.toggle()
        except queue.Empty:pass
        try:
            while True:
                kind,value=self.messages.get_nowait()
                self.worker=None
                if kind=='error':
                    Path(__file__).with_name('recorder_error.log').write_text(value,encoding='utf-8')
                    self.state('录制失败')
                else:self.state('录制结束')
        except queue.Empty:pass
        self.root.after(20,self.poll)

    def close(self):
        self.inputs.active.clear()
        if self.worker:
            self.worker.stop.set();self.worker.thread.join(timeout=5)
        self.inputs.close();self.root.destroy()


def main():
    ctypes.windll.user32.SetProcessDPIAware()
    kernel32=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel32.CreateMutexW.argtypes=[wintypes.LPVOID,wintypes.BOOL,wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype=wintypes.HANDLE
    mutex=kernel32.CreateMutexW(None,False,'PVZRealGameplayRecorder')
    if ctypes.get_last_error()==183:return
    root=tk.Tk();badge=RecorderBadge(root)
    try:root.mainloop()
    finally:
        if badge.worker:
            badge.worker.stop.set();badge.worker.thread.join(timeout=5)
        badge.inputs.close()
        kernel32.CloseHandle.argtypes=[wintypes.HANDLE];kernel32.CloseHandle(mutex)

if __name__=='__main__':
    try:main()
    except Exception:Path(__file__).with_name('recorder_error.log').write_text(traceback.format_exc(),encoding='utf-8')
