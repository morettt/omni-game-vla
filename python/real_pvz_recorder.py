"""500 ms desktop recorder with selectable mouse and keyboard input."""
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
from recording_input import RecordingInput, key_name

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'真正PVZ'/'全新录制'
KEYBOARD_DATA=ROOT/'真正PVZ'/'键盘录制'
MODES={'记录鼠标':'mouse', '记录键盘':'keyboard'}
KEYBOARD_STEP_MS=100
KEYBOARD_STEPS=5


class RecordingStore:
    def __init__(self,folder,mode='mouse'):
        self.folder=Path(folder);self.images=self.folder/'images'
        self.mode=mode
        self.images.mkdir(parents=True,exist_ok=True)
        self.path=self.folder/'data.json'
        self.rows=json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else []
        if not isinstance(self.rows,list):raise ValueError('已有录制 JSON 格式错误')
        self.next_index=max([0]+[int(p.stem) for p in self.images.glob('*.png') if p.stem.isdigit()])+1
        self.ends=[];self.indices=[];self.segment=round(time.time()*1000)
        self.held_keys={}
        self.frame_ticks=[]

    def add_frame(self,image,completed,wall,bounds):
        name=f'images/{self.next_index:08d}.png';path=self.folder/name
        temporary=path.with_suffix('.png.tmp')
        image.convert('RGB').resize((224,224),Image.Resampling.BILINEAR).save(temporary,format='PNG',compress_level=1)
        temporary.replace(path)
        event_key='点击' if self.mode=='mouse' else '按键'
        row={'图片':name,'时间戳':round(wall*1000),'录制段':self.segment,
             '屏幕区域':bounds,event_key:[]}
        if self.mode=='keyboard':
            row['按键分段']=[[] for _ in range(KEYBOARD_STEPS)]
            self.frame_ticks.append(round(completed*1000))
        self.rows.append(row);self.ends.append(completed);self.indices.append(len(self.rows)-1)
        self.next_index+=1

    def add_click(self,click):
        return self.add_event('点击', click, {'横坐标':click['x'],'纵坐标':click['y']})

    def add_key(self,press):
        name=key_name(press['vk'],press['scan'],press['extended'])
        identity=(press['vk'],press['scan'],press['extended'])
        stamp=round(press['perf']*1000)
        down=press['down']
        if down:
            self.held_keys[identity]=(name,stamp)
        else:
            held=self.held_keys.pop(identity,None)
            if held is not None:
                self.mark_hold(*held,stamp)
        return self.add_event('按键',press,{'键名':name,'动作':'按下' if down else '松开'})

    def mark_hold(self,name,start,end):
        if end <= start:
            return
        first=max(0,bisect_right(self.frame_ticks,start)-1)
        last=bisect_right(self.frame_ticks,end-1)
        for index in range(first,last):
            anchor=self.frame_ticks[index]
            limit=anchor+KEYBOARD_STEPS*KEYBOARD_STEP_MS
            if index+1 < len(self.frame_ticks):
                limit=min(limit,self.frame_ticks[index+1])
            overlap_start=max(start,anchor)
            overlap_end=min(end,limit)
            if overlap_start >= overlap_end:
                continue
            start_step=(overlap_start-anchor)//KEYBOARD_STEP_MS
            end_step=(overlap_end-1-anchor)//KEYBOARD_STEP_MS
            steps=self.rows[self.indices[index]]['按键分段']
            for step in steps[start_step:end_step+1]:
                if name not in step:
                    step.append(name)
                    step.sort()

    def finish_keys(self,completed):
        end=round(completed*1000)
        for name,start in self.held_keys.values():
            self.mark_hold(name,start,end)
        self.held_keys.clear()

    def add_event(self,key,event,label):
        index=bisect_right(self.ends,event['perf'])-1
        if index<0:return False # No screenshot existed before this action.
        label['时间戳']=round(event['wall']*1000)
        self.rows[self.indices[index]][key].append(label)
        return True

    def save(self):
        temporary=self.path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(self.rows,ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(self.path)


class CaptureWorker:
    def __init__(self,folder,inputs,messages,grab=None,bounds=None,mode='mouse'):
        self.folder=folder;self.inputs=inputs;self.messages=messages
        self.mode=mode
        self.events=inputs.clicks if mode=='mouse' else inputs.keys
        self.grab=grab or (lambda:ImageGrab.grab(all_screens=True))
        self.bounds=bounds or self.screen_bounds
        self.stop=threading.Event();self.thread=threading.Thread(target=self.run,daemon=True)

    @staticmethod
    def screen_bounds(image):
        user32=ctypes.windll.user32
        return {'左':user32.GetSystemMetrics(76),'上':user32.GetSystemMetrics(77),'宽':image.width,'高':image.height}

    def drain(self,store):
        append=store.add_click if self.mode=='mouse' else store.add_key
        while True:
            try:event=self.events.get_nowait()
            except queue.Empty:return
            append(event)

    def run(self):
        store=None
        try:
            store=RecordingStore(self.folder,self.mode)
            while True:
                try:self.events.get_nowait()
                except queue.Empty:break
            self.inputs.mode=self.mode
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
            self.inputs.active.clear();self.drain(store)
            store.finish_keys(time.perf_counter())
            store.save()
            self.messages.put(('stopped',len(store.ends)))
        except Exception:
            self.inputs.active.clear()
            if store is not None:
                try:
                    self.drain(store)
                    store.finish_keys(time.perf_counter())
                    store.save()
                except Exception:pass
            self.messages.put(('error',traceback.format_exc()))


class RecorderBadge:
    WIDTH = 608
    HEIGHT = 108
    MENU_TOP = 116
    ROW_HEIGHT = 80
    MENU_HEIGHT = 284
    PANEL = '#f5f8f6'
    BORDER = '#284738'
    TEXT = '#16251d'
    MUTED = '#527465'
    FONT = ('Microsoft YaHei UI', -36)

    def __init__(self,root,inputs=None):
        self.root=root;self.worker=None;self.messages=queue.Queue()
        self.mode='记录鼠标'
        self.menu_open=False
        self.leave_timer=None
        root.overrideredirect(True);root.attributes('-topmost',True)
        root.configure(bg=self.PANEL)
        self.left=root.winfo_screenwidth()-self.WIDTH-20
        root.geometry(f'{self.WIDTH}x{self.HEIGHT}+{self.left}+20')
        self.canvas=tk.Canvas(root,width=self.WIDTH,height=self.HEIGHT,bg=self.PANEL,
                              highlightthickness=0,borderwidth=0)
        self.canvas.pack()
        self.background=self.canvas.create_rectangle(2,2,self.WIDTH-2,self.HEIGHT-2,
                                                     fill=self.PANEL,outline=self.BORDER,width=4)
        self.dot=self.canvas.create_oval(32,42,56,66,fill=self.MUTED,outline='')
        self.label=self.canvas.create_text(78,54,text='录制结束',fill=self.TEXT,font=self.FONT,anchor='w')
        self.canvas.create_line(282,22,282,86,fill='#b1c2b9',width=2)
        self.mode_label=self.canvas.create_text(318,54,text=self.mode,fill=self.TEXT,font=self.FONT,anchor='w')
        self.chevron=self.canvas.create_line(540,48,552,60,564,48,fill=self.BORDER,width=4)
        self.canvas.bind('<Button-1>',self.click)
        self.canvas.bind('<Enter>',self.cancel_leave)
        self.canvas.bind('<Leave>',self.leave)
        root.bind('<Escape>',lambda _:self.show_menu(False))
        root.update_idletasks()
        user32=ctypes.WinDLL('user32',use_last_error=True)
        user32.GetAncestor.argtypes=[wintypes.HWND,ctypes.c_uint];user32.GetAncestor.restype=wintypes.HWND
        hwnd=user32.GetAncestor(root.winfo_id(),2)
        user32.GetWindowLongW.argtypes=[wintypes.HWND,ctypes.c_int]
        user32.SetWindowLongW.argtypes=[wintypes.HWND,ctypes.c_int,ctypes.c_long]
        style=user32.GetWindowLongW(hwnd,-20)
        user32.SetWindowLongW(hwnd,-20,(style|0x08000000|0x00000080)&~0x00000020)
        user32.SetWindowDisplayAffinity.argtypes=[wintypes.HWND,wintypes.DWORD]
        if not user32.SetWindowDisplayAffinity(hwnd,0x11):raise ctypes.WinError(ctypes.get_last_error())
        self.inputs=inputs or RecordingInput()
        root.protocol('WM_DELETE_WINDOW',self.close)
        root.after(20,self.poll)

    def click(self,event):
        if self.worker is not None:
            return
        if event.y < self.HEIGHT:
            self.show_menu(not self.menu_open)
        elif self.menu_open:
            index=(event.y-self.MENU_TOP)//self.ROW_HEIGHT
            if 0 <= index < len(MODES):
                self.mode=tuple(MODES)[index]
                self.canvas.itemconfigure(self.mode_label,text=self.mode)
                self.show_menu(False)

    def show_menu(self,opened):
        self.cancel_leave()
        self.menu_open=opened
        self.canvas.delete('menu')
        height=self.MENU_HEIGHT if opened else self.HEIGHT
        self.root.geometry(f'{self.WIDTH}x{height}+{self.left}+20')
        self.canvas.configure(height=height)
        edge,center=(60,48) if opened else (48,60)
        self.canvas.coords(self.chevron,540,edge,552,center,564,edge)
        if not opened:
            return
        self.canvas.create_rectangle(2,self.HEIGHT-4,self.WIDTH-2,self.MENU_HEIGHT-2,
                                     fill=self.PANEL,outline=self.BORDER,width=4,tags='menu')
        for index,mode in enumerate(MODES):
            top=self.MENU_TOP+index*self.ROW_HEIGHT
            if mode==self.mode:
                self.canvas.create_rectangle(16,top+4,self.WIDTH-16,top+self.ROW_HEIGHT-4,
                                             fill='#d8ebdf',outline='',tags='menu')
                self.canvas.create_line(534,top+38,546,top+50,566,top+28,
                                        fill=self.BORDER,width=4,tags='menu')
            self.canvas.create_text(38,top+self.ROW_HEIGHT//2,text=mode,fill=self.TEXT,
                                    font=self.FONT,anchor='w',tags='menu')

    def cancel_leave(self,_=None):
        if self.leave_timer is not None:
            self.root.after_cancel(self.leave_timer)
            self.leave_timer=None

    def leave(self,_):
        if self.menu_open:
            self.cancel_leave()
            self.leave_timer=self.root.after(250,lambda:self.show_menu(False))

    def state(self,text,recording=False):
        self.canvas.itemconfigure(self.label,text=text)
        self.canvas.itemconfigure(self.dot,fill='#bd291f' if recording else self.MUTED)
        self.canvas.itemconfigure(self.background,fill='#ffe4df' if recording else self.PANEL)

    def toggle(self):
        if self.worker is not None:
            self.inputs.active.clear();self.worker.stop.set();self.state('录制结束')
            return
        self.show_menu(False)
        mode=MODES[self.mode]
        folder=DATA if mode=='mouse' else KEYBOARD_DATA
        self.canvas.itemconfigure(self.chevron,state='hidden')
        self.worker=CaptureWorker(folder,self.inputs,self.messages,mode=mode)
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
                self.canvas.itemconfigure(self.chevron,state='normal')
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
