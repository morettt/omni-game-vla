"""Observe physical left clicks and Ctrl+K without swallowing game input."""
import ctypes
from ctypes import wintypes
import queue
import threading
import time


class MouseInfo(ctypes.Structure):
    _fields_=[('pt',wintypes.POINT),('mouseData',wintypes.DWORD),('flags',wintypes.DWORD),
              ('time',wintypes.DWORD),('extra',ctypes.c_size_t)]


class RecordingInput:
    def __init__(self):
        self.active=threading.Event();self.stop=threading.Event();self.ready=threading.Event()
        self.clicks=queue.Queue();self.hotkeys=queue.Queue();self.error=None
        self.thread=threading.Thread(target=self.run,daemon=True)
        self.thread.start()
        if not self.ready.wait(3):raise RuntimeError('录制快捷键启动超时')
        if self.error:raise RuntimeError(self.error)

    def run(self):
        user32=ctypes.WinDLL('user32',use_last_error=True)
        kernel32=ctypes.WinDLL('kernel32',use_last_error=True)
        callback_type=ctypes.WINFUNCTYPE(ctypes.c_ssize_t,ctypes.c_int,wintypes.WPARAM,wintypes.LPARAM)
        user32.SetWindowsHookExW.argtypes=[ctypes.c_int,callback_type,wintypes.HINSTANCE,wintypes.DWORD]
        user32.SetWindowsHookExW.restype=wintypes.HANDLE
        user32.CallNextHookEx.argtypes=[wintypes.HANDLE,ctypes.c_int,wintypes.WPARAM,wintypes.LPARAM]
        user32.CallNextHookEx.restype=ctypes.c_ssize_t
        user32.UnhookWindowsHookEx.argtypes=[wintypes.HANDLE]
        user32.RegisterHotKey.argtypes=[wintypes.HWND,ctypes.c_int,wintypes.UINT,wintypes.UINT]
        user32.UnregisterHotKey.argtypes=[wintypes.HWND,ctypes.c_int]
        user32.PeekMessageW.argtypes=[ctypes.POINTER(wintypes.MSG),wintypes.HWND,wintypes.UINT,wintypes.UINT,wintypes.UINT]
        user32.TranslateMessage.argtypes=[ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.argtypes=[ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.restype=ctypes.c_ssize_t
        kernel32.GetModuleHandleW.argtypes=[wintypes.LPCWSTR];kernel32.GetModuleHandleW.restype=wintypes.HMODULE
        kernel32.GetTickCount64.restype=ctypes.c_ulonglong
        hook=None;registered=False;exit_registered=False
        def mouse(code,message,data):
            try:
                if code>=0 and message==0x0201 and self.active.is_set():
                    info=ctypes.cast(data,ctypes.POINTER(MouseInfo)).contents
                    if not info.flags&1: # Ignore synthetic clicks from model control.
                        age=((kernel32.GetTickCount64()-info.time)&0xffffffff)/1000
                        self.clicks.put(dict(x=info.pt.x,y=info.pt.y,perf=time.perf_counter()-age,wall=time.time()-age))
            finally:
                return user32.CallNextHookEx(None,code,message,data)
        self.callback=callback_type(mouse)
        try:
            registered=bool(user32.RegisterHotKey(None,2,0x0002|0x4000,ord('K')))
            if not registered:raise OSError('Ctrl+K 已被其他程序占用')
            exit_registered=bool(user32.RegisterHotKey(None,3,0x0002|0x0004|0x4000,ord('K')))
            if not exit_registered:raise OSError('Ctrl+Shift+K 已被其他程序占用')
            hook=user32.SetWindowsHookExW(14,self.callback,kernel32.GetModuleHandleW(None),0)
            if not hook:raise ctypes.WinError(ctypes.get_last_error())
            self.ready.set();message=wintypes.MSG()
            while not self.stop.wait(.01):
                while user32.PeekMessageW(ctypes.byref(message),None,0,0,1):
                    if message.message==0x0312 and message.wParam==2:self.hotkeys.put(True)
                    if message.message==0x0312 and message.wParam==3:self.hotkeys.put('exit')
                    user32.TranslateMessage(ctypes.byref(message));user32.DispatchMessageW(ctypes.byref(message))
        except Exception as error:
            self.error=str(error);self.ready.set()
        finally:
            if hook:user32.UnhookWindowsHookEx(hook)
            if registered:user32.UnregisterHotKey(None,2)
            if exit_registered:user32.UnregisterHotKey(None,3)

    def close(self):
        self.active.clear();self.stop.set();self.thread.join(timeout=2)
