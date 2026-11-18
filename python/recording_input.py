"""Observe the selected physical input and pass it on to the game."""
import ctypes
from ctypes import wintypes
from functools import lru_cache
import queue
import threading
import time


class MouseInfo(ctypes.Structure):
    _fields_=[('pt',wintypes.POINT),('mouseData',wintypes.DWORD),('flags',wintypes.DWORD),
              ('time',wintypes.DWORD),('extra',ctypes.c_size_t)]


class KeyInfo(ctypes.Structure):
    _fields_ = [('vk', wintypes.DWORD), ('scan', wintypes.DWORD), ('flags', wintypes.DWORD),
                ('time', wintypes.DWORD), ('extra', ctypes.c_size_t)]


CONTROL_KEYS = {0x11, 0xA2, 0xA3}
SHIFT_KEYS = {0x10, 0xA0, 0xA1}
MODIFIER_KEYS = CONTROL_KEYS | SHIFT_KEYS
OTHER_MODIFIERS = {0x12, 0x5B, 0x5C, 0xA4, 0xA5}

KEY_NAMES = {
    0x08: '退格', 0x09: 'Tab', 0x0D: '回车', 0x10: 'Shift', 0x11: 'Ctrl', 0x12: 'Alt',
    0x13: 'Pause', 0x14: 'Caps Lock', 0x1B: 'Esc', 0x20: '空格',
    0x21: 'Page Up', 0x22: 'Page Down', 0x23: 'End', 0x24: 'Home',
    0x25: '左方向键', 0x26: '上方向键', 0x27: '右方向键', 0x28: '下方向键',
    0x2C: 'Print Screen', 0x2D: 'Insert', 0x2E: 'Delete', 0x5B: '左Win', 0x5C: '右Win',
    0x6A: '小键盘*', 0x6B: '小键盘+', 0x6D: '小键盘-', 0x6E: '小键盘.', 0x6F: '小键盘/',
    0x90: 'Num Lock', 0x91: 'Scroll Lock',
    0xA0: '左Shift', 0xA1: '右Shift', 0xA2: '左Ctrl', 0xA3: '右Ctrl', 0xA4: '左Alt', 0xA5: '右Alt',
}


@lru_cache(maxsize=256)
def key_name(vk, scan, extended):
    if ord('0') <= vk <= ord('9') or ord('A') <= vk <= ord('Z'):
        return chr(vk)
    if 0x70 <= vk <= 0x87:
        return f'F{vk - 0x6F}'
    if 0x60 <= vk <= 0x69:
        return f'小键盘{vk - 0x60}'
    if vk == 0x0D and extended:
        return '小键盘回车'
    if vk in KEY_NAMES:
        return KEY_NAMES[vk]

    user32 = ctypes.WinDLL('user32', use_last_error=True)
    get_name = user32.GetKeyNameTextW
    get_name.argtypes = [wintypes.LONG, wintypes.LPWSTR, ctypes.c_int]
    get_name.restype = ctypes.c_int
    buffer = ctypes.create_unicode_buffer(80)
    packed_scan = (scan & 0xFF) << 16 | (int(extended) << 24)
    if get_name(packed_scan, buffer, len(buffer)):
        return buffer.value
    return '未识别按键'


class KeyPresses:
    """Filter auto-repeat and the recorder's own Ctrl+K hotkeys."""

    def __init__(self):
        self.held = set()
        self.pending_modifiers = {}
        self.reported = set()

    def reset(self):
        self.held.clear()
        self.pending_modifiers.clear()
        self.reported.clear()

    def event(self, vk, down, timestamp):
        if not down:
            self.held.discard(vk)
            pending = self.pending_modifiers.pop(vk, None)
            if pending is not None:
                return [pending, timestamp]
            if vk in self.reported:
                self.reported.remove(vk)
                return [timestamp]
            return []
        if vk in self.held:
            return []
        self.held.add(vk)
        if vk == ord('K') and self.held & CONTROL_KEYS and not self.held & OTHER_MODIFIERS:
            self.pending_modifiers.clear()
            return []
        if vk in MODIFIER_KEYS:
            self.pending_modifiers[vk] = timestamp
            return []
        presses = list(self.pending_modifiers.values()) + [timestamp]
        self.reported.update(self.pending_modifiers)
        self.reported.add(vk)
        self.pending_modifiers.clear()
        return presses


class RecordingInput:
    def __init__(self):
        self.active=threading.Event();self.stop=threading.Event();self.ready=threading.Event()
        self.clicks=queue.Queue();self.hotkeys=queue.Queue();self.error=None
        self.keys=queue.Queue();self.mode='mouse'
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
        hooks=[];registered=False;exit_registered=False
        key_presses = KeyPresses()

        def timestamps(event_time):
            age = ((kernel32.GetTickCount64() - event_time) & 0xffffffff) / 1000
            return {'perf': time.perf_counter() - age, 'wall': time.time() - age}

        def mouse(code,message,data):
            try:
                if code>=0 and message==0x0201 and self.active.is_set() and self.mode=='mouse':
                    info=ctypes.cast(data,ctypes.POINTER(MouseInfo)).contents
                    if not info.flags&1: # Ignore synthetic clicks from model control.
                        self.clicks.put(dict(x=info.pt.x,y=info.pt.y,**timestamps(info.time)))
            finally:
                return user32.CallNextHookEx(None,code,message,data)

        def keyboard(code, message, data):
            try:
                if not self.active.is_set() or self.mode != 'keyboard':
                    key_presses.reset()
                elif code >= 0 and message in (0x0100, 0x0101, 0x0104, 0x0105):
                    info = ctypes.cast(data, ctypes.POINTER(KeyInfo)).contents
                    if not info.flags & 0x10:
                        down = message in (0x0100, 0x0104)
                        event = dict(vk=info.vk, scan=info.scan, extended=bool(info.flags & 1), down=down,
                                     **timestamps(info.time))
                        for press in key_presses.event(info.vk, down, event):
                            self.keys.put(press)
            finally:
                return user32.CallNextHookEx(None, code, message, data)

        callbacks = [(14, callback_type(mouse)), (13, callback_type(keyboard))]
        try:
            registered=bool(user32.RegisterHotKey(None,2,0x0002|0x4000,ord('K')))
            if not registered:raise OSError('Ctrl+K 已被其他程序占用')
            exit_registered=bool(user32.RegisterHotKey(None,3,0x0002|0x0004|0x4000,ord('K')))
            if not exit_registered:raise OSError('Ctrl+Shift+K 已被其他程序占用')
            for hook_type, callback in callbacks:
                hook = user32.SetWindowsHookExW(hook_type, callback, kernel32.GetModuleHandleW(None), 0)
                if not hook:
                    raise ctypes.WinError(ctypes.get_last_error())
                hooks.append(hook)
            self.ready.set();message=wintypes.MSG()
            while not self.stop.wait(.01):
                while user32.PeekMessageW(ctypes.byref(message),None,0,0,1):
                    if message.message==0x0312 and message.wParam==2:self.hotkeys.put(True)
                    if message.message==0x0312 and message.wParam==3:self.hotkeys.put('exit')
                    user32.TranslateMessage(ctypes.byref(message));user32.DispatchMessageW(ctypes.byref(message))
        except Exception as error:
            self.error=str(error);self.ready.set()
        finally:
            for hook in hooks:
                user32.UnhookWindowsHookEx(hook)
            if registered:user32.UnregisterHotKey(None,2)
            if exit_registered:user32.UnregisterHotKey(None,3)

    def close(self):
        self.active.clear();self.stop.set();self.thread.join(timeout=2)
