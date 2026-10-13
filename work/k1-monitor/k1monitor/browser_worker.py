"""A dedicated, windowless Edge process, owned by a Windows job object."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess


class _Limits(ctypes.Structure):
    _fields_ = [("user_time", ctypes.c_int64), ("kernel_time", ctypes.c_int64),
                ("flags", wintypes.DWORD), ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t),
                ("active_processes", wintypes.DWORD), ("affinity", ctypes.c_size_t),
                ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]


class _IO(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]


class _Extended(ctypes.Structure):
    _fields_ = [("basic", _Limits), ("io", _IO), ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]


class _Accounting(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int64) for name in ("user_time", "kernel_time", "period_user_time", "period_kernel_time")] + [
        (name, wintypes.DWORD) for name in ("page_faults", "total_processes", "active_processes", "terminated_processes")]


class BrowserWorker:
    def __init__(self, cache: Path):
        self.cache = cache
        self.process = None
        self.job = None

    def running(self):
        if not self.job:
            return False
        # Edge's initial launcher can exit while its browser and renderer remain
        # alive in our job. Testing only Popen.poll() would interrupt other cameras.
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        info = _Accounting()
        if not kernel.QueryInformationJobObject(self.job, 1, ctypes.byref(info), ctypes.sizeof(info), None):
            raise OSError("Не удалось проверить процесс передачи камеры")
        return info.active_processes > 0

    def start(self, url):
        if self.running():
            return
        self.stop()
        if os.name != "nt":
            raise OSError("Передача камеры в этой сборке требует Windows и Microsoft Edge")
        candidates = [Path(os.environ.get(name, "C:/")) / "Microsoft/Edge/Application/msedge.exe"
                      for name in ("ProgramFiles(x86)", "ProgramFiles")]
        executable = next((path for path in candidates if path.is_file()), None)
        if not executable:
            raise OSError("Для передачи камеры нужен установленный Microsoft Edge")
        self.cache.mkdir(parents=True, exist_ok=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        job = kernel.CreateJobObjectW(None, None)
        limits = _Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not job or not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            if job: kernel.CloseHandle(job)
            raise OSError("Не удалось создать процесс передачи камеры")
        try:
            process = subprocess.Popen([str(executable), "--headless=new", "--no-first-run",
                "--no-default-browser-check", "--disable-extensions", "--disable-sync",
                "--disable-background-networking", "--autoplay-policy=no-user-gesture-required",
                "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
                "--disable-backgrounding-occluded-windows", "--mute-audio", "--window-size=800,600",
                "--user-data-dir=" + str(self.cache.resolve()), url],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW)
            if not kernel.AssignProcessToJobObject(job, wintypes.HANDLE(int(process._handle))):
                process.kill()
                process.wait(timeout=5)
                raise OSError("Не удалось привязать процесс камеры к программе")
            self.process, self.job = process, job
        except BaseException:
            kernel.CloseHandle(job)
            raise

    def stop(self):
        if self.job:
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle(self.job)
            self.job = None
        if self.process:
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None
