"""Native Windows notification icon. No HTTP lifecycle controls or dependencies."""
import ctypes as c
from ctypes import wintypes as w
import logging
import os
from pathlib import Path
import threading
from collections import deque

LOG=logging.getLogger(__name__)
MENU=((1001,'Открыть программу','open'),(1002,'Перезапуск службы','restart'),(1003,'Остановить службу','stop'))


class InstanceLease:
    """Hold one local-user process per port, including the gap during a restart."""
    def __init__(self,port):
        self.handle=None;self.owned=False
        if os.name!='nt':self.owned=True;return
        self.kernel=c.WinDLL('kernel32',use_last_error=True)
        self.kernel.CreateMutexW.argtypes=[c.c_void_p,w.BOOL,w.LPCWSTR];self.kernel.CreateMutexW.restype=w.HANDLE
        self.kernel.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD];self.kernel.WaitForSingleObject.restype=w.DWORD
        self.kernel.ReleaseMutex.argtypes=[w.HANDLE];self.kernel.ReleaseMutex.restype=w.BOOL
        self.kernel.CloseHandle.argtypes=[w.HANDLE];self.kernel.CloseHandle.restype=w.BOOL
        self.handle=self.kernel.CreateMutexW(None,False,f'Local\\K1Monitor-port-{port}')
        if not self.handle:raise c.WinError(c.get_last_error())
        self.owned=self.kernel.WaitForSingleObject(self.handle,0) in (0,0x80)

    def close(self):
        if self.handle:
            if self.owned:self.kernel.ReleaseMutex(self.handle)
            self.kernel.CloseHandle(self.handle);self.handle=None


class WindowsTray:
    def __init__(self,control,open_program,*,title='3DMonitor',translate=lambda text:text):
        self.control=control;self.open_program=open_program;self.title=title
        self.translate=translate
        self.ready=threading.Event();self.thread=None;self.error=None
        self.hwnd=None;self.menu=None;self.icon=None;self.registered=False
        self.class_name=f'3DMonitorTray-{os.getpid()}-{id(self)}'
        self.notifications=deque(maxlen=100);self.notification_lock=threading.Lock();self.next_notification=0

    def notify(self,title,message,warning=False):
        if not self.hwnd or not self.registered:return False
        with self.notification_lock:self.notifications.append((title,message,warning))
        self.user.PostMessageW(self.hwnd,0x8002,0,0)
        return True

    def show_notification(self):
        import time
        if time.monotonic()<self.next_notification:return
        with self.notification_lock:
            if not self.notifications:return
            title,message,warning=self.notifications.popleft()
        # Use a separate structure: tooltip updates must not replay szInfo.
        info=self.NID(cbSize=c.sizeof(self.NID),hWnd=self.hwnd,uID=1,uFlags=0x10,
                      szInfoTitle=title[:63],szInfo=message[:255],dwInfoFlags=2 if warning else 1)
        self.shell.Shell_NotifyIconW(1,c.byref(info));self.next_notification=time.monotonic()+6

    def activate(self,command):
        action=next((item[2] for item in MENU if item[0]==command),None)
        if action=='open':
            threading.Thread(target=self.open_program,name='open-k1monitor',daemon=True).start()
        elif action and self.control.request(action):LOG.info('Tray requested service %s',action)

    def start(self):
        if os.name!='nt':return
        self.thread=threading.Thread(target=self._run,name='windows-tray',daemon=True)
        self.thread.start()
        if not self.ready.wait(5):raise RuntimeError('Значок Windows не отвечает')
        if self.error:raise RuntimeError('Не удалось создать значок Windows') from self.error

    def stop(self):
        if self.hwnd:self.user.PostMessageW(self.hwnd,0x10,0,0)
        if self.thread:self.thread.join(5)

    def _setup(self):
        self.user=c.WinDLL('user32',use_last_error=True);self.shell=c.WinDLL('shell32',use_last_error=True);self.kernel=c.WinDLL('kernel32',use_last_error=True)
        self.callback_type=c.WINFUNCTYPE(c.c_ssize_t,w.HWND,w.UINT,c.c_size_t,c.c_ssize_t)
        class GUID(c.Structure):
            _fields_=[('Data1',w.DWORD),('Data2',w.WORD),('Data3',w.WORD),('Data4',w.BYTE*8)]
        class WNDCLASS(c.Structure):
            _fields_=[('style',w.UINT),('lpfnWndProc',self.callback_type),('cbClsExtra',c.c_int),('cbWndExtra',c.c_int),('hInstance',w.HINSTANCE),('hIcon',w.HICON),('hCursor',w.HANDLE),('hbrBackground',w.HBRUSH),('lpszMenuName',w.LPCWSTR),('lpszClassName',w.LPCWSTR)]
        class NID(c.Structure):
            _fields_=[('cbSize',w.DWORD),('hWnd',w.HWND),('uID',w.UINT),('uFlags',w.UINT),('uCallbackMessage',w.UINT),('hIcon',w.HICON),('szTip',w.WCHAR*128),('dwState',w.DWORD),('dwStateMask',w.DWORD),('szInfo',w.WCHAR*256),('uVersion',w.UINT),('szInfoTitle',w.WCHAR*64),('dwInfoFlags',w.DWORD),('guidItem',GUID),('hBalloonIcon',w.HICON)]
        class Identifier(c.Structure):
            _fields_=[('cbSize',w.DWORD),('hWnd',w.HWND),('uID',w.UINT),('guidItem',GUID)]
        self.WNDCLASS=WNDCLASS;self.NID=NID;self.Identifier=Identifier
        signatures={
            'RegisterClassW':([c.POINTER(WNDCLASS)],w.ATOM),
            'UnregisterClassW':([w.LPCWSTR,w.HINSTANCE],w.BOOL),
            'CreateWindowExW':([w.DWORD,w.LPCWSTR,w.LPCWSTR,w.DWORD,c.c_int,c.c_int,c.c_int,c.c_int,w.HWND,w.HMENU,w.HINSTANCE,c.c_void_p],w.HWND),
            'DefWindowProcW':([w.HWND,w.UINT,c.c_size_t,c.c_ssize_t],c.c_ssize_t),
            'DestroyWindow':([w.HWND],w.BOOL),'PostMessageW':([w.HWND,w.UINT,c.c_size_t,c.c_ssize_t],w.BOOL),
            'GetMessageW':([c.POINTER(w.MSG),w.HWND,w.UINT,w.UINT],c.c_int),
            'TranslateMessage':([c.POINTER(w.MSG)],w.BOOL),'DispatchMessageW':([c.POINTER(w.MSG)],c.c_ssize_t),
            'PostQuitMessage':([c.c_int],None),'RegisterWindowMessageW':([w.LPCWSTR],w.UINT),
            'CreatePopupMenu':([],w.HMENU),'AppendMenuW':([w.HMENU,w.UINT,c.c_size_t,w.LPCWSTR],w.BOOL),
            'ModifyMenuW':([w.HMENU,w.UINT,w.UINT,c.c_size_t,w.LPCWSTR],w.BOOL),
            'SetMenuDefaultItem':([w.HMENU,w.UINT,w.UINT],w.BOOL),'EnableMenuItem':([w.HMENU,w.UINT,w.UINT],w.BOOL),
            'GetMenuStringW':([w.HMENU,w.UINT,w.LPWSTR,c.c_int,w.UINT],c.c_int),
            'DestroyMenu':([w.HMENU],w.BOOL),'SetForegroundWindow':([w.HWND],w.BOOL),
            'TrackPopupMenu':([w.HMENU,w.UINT,c.c_int,c.c_int,c.c_int,w.HWND,c.c_void_p],w.UINT),
            'GetCursorPos':([c.POINTER(w.POINT)],w.BOOL),'SetTimer':([w.HWND,c.c_size_t,w.UINT,c.c_void_p],c.c_size_t),
            'LoadImageW':([w.HINSTANCE,w.LPCWSTR,w.UINT,c.c_int,c.c_int,w.UINT],w.HANDLE),
            'DestroyIcon':([w.HICON],w.BOOL),'GetSystemMetrics':([c.c_int],c.c_int)}
        for name,(args,result) in signatures.items():
            fn=getattr(self.user,name);fn.argtypes=args;fn.restype=result
        self.kernel.GetModuleHandleW.argtypes=[w.LPCWSTR];self.kernel.GetModuleHandleW.restype=w.HMODULE
        self.shell.Shell_NotifyIconW.argtypes=[w.DWORD,c.POINTER(NID)];self.shell.Shell_NotifyIconW.restype=w.BOOL
        self.shell.Shell_NotifyIconGetRect.argtypes=[c.POINTER(Identifier),c.POINTER(w.RECT)];self.shell.Shell_NotifyIconGetRect.restype=c.c_long

    def _add_icon(self):
        self.nid.szTip=(self.title+' · '+self.translate(self.control.status))[:127]
        if not self.shell.Shell_NotifyIconW(0,c.byref(self.nid)):raise c.WinError(c.get_last_error())
        self.registered=True;self.nid.uVersion=4
        if not self.shell.Shell_NotifyIconW(4,c.byref(self.nid)):raise c.WinError(c.get_last_error())
        self.last_status=(self.control.status,self.translate(self.control.status))

    def inspect(self):
        """Read-only native diagnostics for registration and exact menu labels."""
        if not self.hwnd:return {'registered':False}
        identifier=self.Identifier(cbSize=c.sizeof(self.Identifier),hWnd=self.hwnd,uID=1);rect=w.RECT()
        result=self.shell.Shell_NotifyIconGetRect(c.byref(identifier),c.byref(rect))
        labels=[]
        for key,_,_ in MENU:
            buffer=c.create_unicode_buffer(128);self.user.GetMenuStringW(self.menu,key,buffer,128,0);labels.append(buffer.value)
        return {'pid':os.getpid(),'hwnd':self.hwnd,'registered':self.registered,'icon_rect_available':result==0,'menu':labels,'status':self.control.status}

    def _message(self,hwnd,message,wp,lp):
        try:
            if message==0x8002:self.show_notification();return 0
            if message==self.taskbar_created:
                self._add_icon();return 0
            if message==0x8001:
                event=lp&0xffff
                if event==0x7b:
                    point=w.POINT();self.user.GetCursorPos(c.byref(point))
                    for key,label,action in MENU:
                        self.user.ModifyMenuW(self.menu,key,0,key,self.translate(label))
                        disabled=action!='open' and self.control.busy
                        self.user.EnableMenuItem(self.menu,key,1 if disabled else 0)
                    self.user.SetForegroundWindow(hwnd)
                    command=self.user.TrackPopupMenu(self.menu,0x100|0x2,point.x,point.y,0,hwnd,None)
                    self.user.PostMessageW(hwnd,0,0,0)
                    if command:self.activate(command)
                elif event in (0x203,0x401,0x405):self.activate(1001)
                return 0
            if message==0x113:
                self.show_notification()
                status=self.control.status;localized=self.translate(status)
                if (status,localized)!=self.last_status:
                    self.nid.szTip=(self.title+' · '+localized)[:127];self.shell.Shell_NotifyIconW(1,c.byref(self.nid));self.last_status=(status,localized)
                return 0
            if message==0x11:return 1
            if message==0x16 and wp:self.control.request('stop');return 0
            if message==0x10:
                self.shell.Shell_NotifyIconW(2,c.byref(self.nid));self.registered=False;self.user.DestroyWindow(hwnd);return 0
            if message==2:self.user.PostQuitMessage(0);return 0
        except Exception:LOG.exception('Tray callback failed')
        return self.user.DefWindowProcW(hwnd,message,wp,lp)

    def _run(self):
        instance=None;atom=None
        try:
            self._setup();instance=self.kernel.GetModuleHandleW(None)
            self.callback=self.callback_type(self._message)
            cls=self.WNDCLASS(lpfnWndProc=self.callback,hInstance=instance,lpszClassName=self.class_name)
            atom=self.user.RegisterClassW(c.byref(cls))
            if not atom:raise c.WinError(c.get_last_error())
            self.taskbar_created=self.user.RegisterWindowMessageW('TaskbarCreated')
            if not self.taskbar_created:raise c.WinError(c.get_last_error())
            # A hidden top-level window receives TaskbarCreated after Explorer restarts.
            self.hwnd=self.user.CreateWindowExW(0,self.class_name,self.title,0,0,0,0,0,None,None,instance,None)
            if not self.hwnd:raise c.WinError(c.get_last_error())
            size=self.user.GetSystemMetrics(49)
            self.icon=self.user.LoadImageW(None,str(Path(__file__).parent/'static/k1monitor.ico'),1,size,size,0x10)
            if not self.icon:raise c.WinError(c.get_last_error())
            self.menu=self.user.CreatePopupMenu()
            if not self.menu:raise c.WinError(c.get_last_error())
            for key,label,_ in MENU:
                if not self.user.AppendMenuW(self.menu,0,key,self.translate(label)):raise c.WinError(c.get_last_error())
            self.user.SetMenuDefaultItem(self.menu,1001,False)
            self.nid=self.NID(cbSize=c.sizeof(self.NID),hWnd=self.hwnd,uID=1,uFlags=1|2|4|0x80,uCallbackMessage=0x8001,hIcon=self.icon)
            self._add_icon();self.user.SetTimer(self.hwnd,1,1000,None);self.ready.set()
            message=w.MSG()
            while True:
                result=self.user.GetMessageW(c.byref(message),None,0,0)
                if result==0:break
                if result==-1:raise c.WinError(c.get_last_error())
                self.user.TranslateMessage(c.byref(message));self.user.DispatchMessageW(c.byref(message))
        except Exception as exc:self.error=exc;LOG.exception('Windows tray unavailable')
        finally:
            self.ready.set()
            if self.hwnd:
                if self.registered:self.shell.Shell_NotifyIconW(2,c.byref(self.nid))
                self.user.DestroyWindow(self.hwnd);self.hwnd=None;self.registered=False
            if self.menu:self.user.DestroyMenu(self.menu);self.menu=None
            if self.icon:self.user.DestroyIcon(self.icon);self.icon=None
            if atom:self.user.UnregisterClassW(self.class_name,instance)
