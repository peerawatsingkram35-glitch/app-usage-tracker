import ctypes
import csv
import json
import os
import sys
import threading
import time
from ctypes import wintypes
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

BASE_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
CONFIG_FILE = os.path.join(BASE_DIR, "tracker_config.json")
DATA_FILE = os.path.join(BASE_DIR, "usage_data.json")

UNITS = ["วินาที", "นาที", "ชั่วโมง"]
DT_FMT = "%d/%m/%Y %H:%M"
SAVE_EVERY_SEC = 30

DEFAULT_CONFIG = {
    "start": "",
    "end": "",
    "refresh_val": 5,
    "refresh_unit": "วินาที",
    "idle_min": 5
}


# ---------------- Windows API (ผ่าน ctypes ไม่ต้องใช้ pywin32) ----------------
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.GetTickCount.restype = wintypes.DWORD


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
user32.GetLastInputInfo.restype = wintypes.BOOL

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def get_active_app():
    """คืนชื่อไฟล์ .exe ของหน้าต่างที่กำลังใช้งานอยู่"""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

    name = None
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if handle:
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                name = os.path.basename(buf.value)
        finally:
            kernel32.CloseHandle(handle)

    if not name:  # สำรอง: ใช้ชื่อหน้าต่างแทน
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        name = buf.value or f"ไม่ทราบชื่อ (PID {pid.value})"
    return name


def get_idle_seconds():
    """เวลาที่ไม่ได้แตะเมาส์/คีย์บอร์ด (วินาที)"""
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not user32.GetLastInputInfo(ctypes.byref(lii)):
        return 0
    return ((kernel32.GetTickCount() - lii.dwTime) & 0xFFFFFFFF) / 1000


# ---------------- ฟังก์ชันช่วย ----------------
def to_seconds(val, unit):
    if unit == "นาที":
        return float(val) * 60
    if unit == "ชั่วโมง":
        return float(val) * 3600
    return float(val)


def fmt_num(val):
    val = float(val)
    return str(int(val)) if val.is_integer() else str(val)


def fmt_duration(sec):
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def load_config():
    cfg = DEFAULT_CONFIG.copy()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                old = json.load(f)
            cfg.update({k: v for k, v in old.items() if k in DEFAULT_CONFIG})
        except Exception:
            pass
    return cfg


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)


# ---------------- ตัวบันทึกเวลา (ทำงานเบื้องหลัง) ----------------
class UsageTracker:
    def __init__(self):
        self.lock = threading.Lock()
        self.usage = {}
        self.current_app = None
        self.state = "stopped"
        self.start_dt = None
        self.end_dt = None
        self.idle_limit = 0
        self.stop_event = threading.Event()
        self.thread = None
        self.load()

    def load(self):
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.usage = {k: float(v) for k, v in data.get("usage", {}).items()}
            except Exception:
                self.usage = {}

    def save(self):
        with self.lock:
            data = {"updated": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                    "usage": dict(self.usage)}
        try:
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception:
            pass

    def has_data(self):
        with self.lock:
            return bool(self.usage)

    def clear(self):
        with self.lock:
            self.usage = {}
        self.save()

    def snapshot(self):
        with self.lock:
            return dict(self.usage)

    def is_running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, start_dt, end_dt, idle_limit_sec):
        self.start_dt = start_dt
        self.end_dt = end_dt
        self.idle_limit = idle_limit_sec
        self.state = "waiting" if datetime.now() < start_dt else "recording"
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)
        if self.state != "finished":
            self.state = "stopped"
        self.current_app = None

    def _loop(self):
        last = time.monotonic()
        last_save = last
        while not self.stop_event.wait(1):
            now_mono = time.monotonic()
            delta = min(now_mono - last, 2)  # กันเวลากระโดดตอนเครื่อง sleep
            last = now_mono
            now = datetime.now()

            if now < self.start_dt:
                self.state = "waiting"
                self.current_app = None
                continue
            if now >= self.end_dt:
                self.state = "finished"
                self.current_app = None
                break
            if self.idle_limit > 0 and get_idle_seconds() >= self.idle_limit:
                self.state = "idle"
                self.current_app = None
                continue

            app = get_active_app()
            with self.lock:
                self.current_app = app
                self.state = "recording"
                if app:
                    self.usage[app] = self.usage.get(app, 0) + delta

            if now_mono - last_save >= SAVE_EVERY_SEC:
                self.save()
                last_save = now_mono
        self.save()


# ---------------- หน้าต่างโปรแกรม ----------------
class App:
    STATE_TEXT = {
        "stopped": ("หยุดอยู่", "red"),
        "waiting": ("รอถึงเวลาเริ่มบันทึก", "darkorange"),
        "recording": ("กำลังบันทึก", "green"),
        "idle": ("ไม่ได้ใช้งานเครื่อง (หยุดนับชั่วคราว)", "darkorange"),
        "finished": ("ครบเวลาที่กำหนด หยุดบันทึกแล้ว", "blue"),
    }

    def __init__(self, root):
        self.root = root
        self.root.title("App Usage Tracker - บันทึกเวลาใช้งานแอป")
        self.root.geometry("680x660")
        self.root.minsize(580, 520)
        self.cfg = load_config()
        self.tracker = UsageTracker()
        self.next_refresh = 0
        self.last_refresh = datetime.now()
        self.build_ui()
        self.fill_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.tick()

    def build_ui(self):
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill="both", expand=True)

        # ช่วงเวลาบันทึก
        rng = ttk.LabelFrame(main, text="ช่วงเวลาที่ต้องการบันทึก", padding=8)
        rng.pack(fill="x")
        ttk.Label(rng, text="เริ่ม:").grid(row=0, column=0, sticky="w", pady=3)
        self.e_start = ttk.Entry(rng, width=20)
        self.e_start.grid(row=0, column=1, sticky="w", pady=3)
        ttk.Button(rng, text="ตอนนี้", command=self.set_start_now).grid(row=0, column=2, padx=4)

        ttk.Label(rng, text="สิ้นสุด:").grid(row=1, column=0, sticky="w", pady=3)
        self.e_end = ttk.Entry(rng, width=20)
        self.e_end.grid(row=1, column=1, sticky="w", pady=3)
        ttk.Button(rng, text="สิ้นวันนี้", command=self.set_end_today).grid(row=1, column=2, padx=4)

        ttk.Label(rng, text="รูปแบบ: วว/ดด/ปปปป ชช:นน (ปี ค.ศ.) เช่น 25/12/2025 08:30",
                  foreground="gray").grid(row=2, column=0, columnspan=3, sticky="w")

        # การแสดงผล
        st = ttk.LabelFrame(main, text="การแสดงผล", padding=8)
        st.pack(fill="x", pady=6)
        ttk.Label(st, text="รีเฟรชข้อมูลทุก:").grid(row=0, column=0, sticky="w", pady=3)
        self.e_refresh = ttk.Entry(st, width=10)
        self.e_refresh.grid(row=0, column=1, sticky="w")
        self.cb_refresh = ttk.Combobox(st, values=UNITS, width=8, state="readonly")
        self.cb_refresh.grid(row=0, column=2, padx=4, sticky="w")
        ttk.Button(st, text="รีเฟรชตอนนี้", command=self.refresh_now).grid(row=0, column=3, padx=4)
        self.e_refresh.bind("<Return>", lambda e: self.refresh_now())
        self.cb_refresh.bind("<<ComboboxSelected>>", lambda e: self.refresh_now())

        ttk.Label(st, text="ไม่นับเวลาเมื่อไม่ได้ใช้เครื่องเกิน:").grid(row=1, column=0, sticky="w", pady=3)
        self.e_idle = ttk.Entry(st, width=10)
        self.e_idle.grid(row=1, column=1, sticky="w")
        ttk.Label(st, text="นาที (0 = นับตลอด)").grid(row=1, column=2, columnspan=2, sticky="w", padx=4)

        # ปุ่ม
        btns = ttk.Frame(main)
        btns.pack(fill="x", pady=4)
        self.btn_start = ttk.Button(btns, text="เริ่มบันทึก", command=self.start_record)
        self.btn_start.pack(side="left", padx=3)
        self.btn_stop = ttk.Button(btns, text="หยุดบันทึก", command=self.stop_record, state="disabled")
        self.btn_stop.pack(side="left", padx=3)
        ttk.Button(btns, text="ล้างข้อมูล", command=self.clear_data).pack(side="left", padx=3)
        ttk.Button(btns, text="ส่งออก CSV", command=self.export_csv).pack(side="left", padx=3)

        # สถานะ
        info = ttk.Frame(main)
        info.pack(fill="x", pady=4)
        self.lbl_status = ttk.Label(info, text="สถานะ: หยุดอยู่", foreground="red")
        self.lbl_status.pack(anchor="w")
        self.lbl_current = ttk.Label(info, text="แอปที่ใช้อยู่: -")
        self.lbl_current.pack(anchor="w")
        self.lbl_total = ttk.Label(info, text="เวลารวม: 00:00:00")
        self.lbl_total.pack(anchor="w")
        self.lbl_refresh = ttk.Label(info, text="", foreground="gray")
        self.lbl_refresh.pack(anchor="w")

        # ตาราง
        table = ttk.Frame(main)
        table.pack(fill="both", expand=True, pady=4)
        cols = ("rank", "app", "time", "percent")
        self.tree = ttk.Treeview(table, columns=cols, show="headings")
        self.tree.heading("rank", text="#")
        self.tree.heading("app", text="แอปพลิเคชัน")
        self.tree.heading("time", text="เวลาใช้งาน")
        self.tree.heading("percent", text="สัดส่วน")
        self.tree.column("rank", width=40, anchor="center", stretch=False)
        self.tree.column("app", width=220)
        self.tree.column("time", width=100, anchor="center", stretch=False)
        self.tree.column("percent", width=220)
        sb = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    def fill_ui(self):
        c = self.cfg
        now = datetime.now()
        start = c["start"] or now.strftime(DT_FMT)
        end = c["end"] or now.replace(hour=23, minute=59).strftime(DT_FMT)
        self.e_start.insert(0, start)
        self.e_end.insert(0, end)
        self.e_refresh.insert(0, fmt_num(c["refresh_val"]))
        self.cb_refresh.set(c["refresh_unit"] if c["refresh_unit"] in UNITS else "วินาที")
        self.e_idle.insert(0, fmt_num(c["idle_min"]))

    # ---------- ปุ่มช่วยกรอกเวลา ----------
    def set_start_now(self):
        self.e_start.delete(0, tk.END)
        self.e_start.insert(0, datetime.now().strftime(DT_FMT))

    def set_end_today(self):
        self.e_end.delete(0, tk.END)
        self.e_end.insert(0, datetime.now().replace(hour=23, minute=59).strftime(DT_FMT))

    # ---------- การตั้งค่า ----------
    def get_refresh_sec(self):
        try:
            v = float(self.e_refresh.get())
            if v <= 0:
                raise ValueError
            return max(1, to_seconds(v, self.cb_refresh.get()))
        except ValueError:
            return 5

    def save_settings(self):
        self.cfg["start"] = self.e_start.get().strip()
        self.cfg["end"] = self.e_end.get().strip()
        self.cfg["refresh_unit"] = self.cb_refresh.get()
        try:
            self.cfg["refresh_val"] = float(self.e_refresh.get())
        except ValueError:
            pass
        try:
            self.cfg["idle_min"] = float(self.e_idle.get())
        except ValueError:
            pass
        try:
            save_config(self.cfg)
        except Exception:
            pass

    # ---------- การแสดงผล ----------
    def refresh_now(self):
        self.next_refresh = 0

    def update_view(self):
        usage = self.tracker.snapshot()
        total = sum(usage.values())
        self.tree.delete(*self.tree.get_children())
        ranked = sorted(usage.items(), key=lambda x: x[1], reverse=True)
        for i, (app, sec) in enumerate(ranked, 1):
            pct = sec / total * 100 if total else 0
            bar = "█" * int(round(pct / 5))
            self.tree.insert("", "end", values=(i, app, fmt_duration(sec), f"{pct:5.1f}%  {bar}"))
        self.lbl_total.config(text=f"เวลารวม: {fmt_duration(total)}  ({len(usage)} แอป)")
        self.last_refresh = datetime.now()

    def tick(self):
        # สถานะและแอปปัจจุบันอัปเดตทุก 1 วินาที
        text, color = self.STATE_TEXT.get(self.tracker.state, ("-", "black"))
        if self.tracker.state == "waiting" and self.tracker.start_dt:
            text += f" ({self.tracker.start_dt.strftime(DT_FMT)})"
        self.lbl_status.config(text=f"สถานะ: {text}", foreground=color)
        self.lbl_current.config(text=f"แอปที่ใช้อยู่: {self.tracker.current_app or '-'}")

        # ถ้าครบเวลาแล้วหยุดเอง ให้คืนสถานะปุ่ม
        if not self.tracker.is_running() and str(self.btn_stop["state"]) == "normal":
            self.btn_start.config(state="normal")
            self.btn_stop.config(state="disabled")
            self.next_refresh = 0

        # ตารางอัปเดตตามรอบที่ผู้ใช้เลือก
        now = time.time()
        if now >= self.next_refresh:
            self.update_view()
            self.next_refresh = now + self.get_refresh_sec()
        remain = max(0, int(self.next_refresh - now))
        self.lbl_refresh.config(
            text=f"รีเฟรชล่าสุด {self.last_refresh:%H:%M:%S}  |  ครั้งถัดไปใน {fmt_duration(remain)}")

        self.root.after(1000, self.tick)

    # ---------- ปุ่มหลัก ----------
    def start_record(self):
        if self.tracker.is_running():
            return
        try:
            start_dt = datetime.strptime(self.e_start.get().strip(), DT_FMT)
            end_dt = datetime.strptime(self.e_end.get().strip(), DT_FMT)
        except ValueError:
            messagebox.showerror("ผิดพลาด", "รูปแบบวันเวลาไม่ถูกต้อง\n"
                                 "กรุณากรอกแบบ วว/ดด/ปปปป ชช:นน เช่น 25/12/2025 08:30")
            return
        if start_dt.year > 2400 or end_dt.year > 2400:
            messagebox.showerror("ผิดพลาด", "กรุณาใช้ปี ค.ศ. (เช่น 2025) ไม่ใช่ปี พ.ศ.")
            return
        if end_dt <= start_dt:
            messagebox.showerror("ผิดพลาด", "เวลาสิ้นสุดต้องอยู่หลังเวลาเริ่ม")
            return
        if end_dt <= datetime.now():
            messagebox.showerror("ผิดพลาด", "เวลาสิ้นสุดผ่านไปแล้ว")
            return
        try:
            idle_min = float(self.e_idle.get())
            if idle_min < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("ผิดพลาด", "กรุณากรอกเวลาไม่ได้ใช้งานเป็นตัวเลข 0 ขึ้นไป")
            return

        if self.tracker.has_data():
            if messagebox.askyesno("มีข้อมูลเดิมอยู่",
                                   "ต้องการล้างข้อมูลเดิมก่อนเริ่มบันทึกไหม?\n\n"
                                   "ใช่ = เริ่มนับใหม่จาก 0\nไม่ = บันทึกต่อจากข้อมูลเดิม"):
                self.tracker.clear()

        self.save_settings()
        self.tracker.start(start_dt, end_dt, idle_min * 60)
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.refresh_now()

    def stop_record(self):
        self.tracker.stop()
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.refresh_now()

    def clear_data(self):
        if messagebox.askyesno("ยืนยัน", "ต้องการลบข้อมูลเวลาใช้งานทั้งหมดใช่ไหม?"):
            self.tracker.clear()
            self.refresh_now()

    def export_csv(self):
        usage = self.tracker.snapshot()
        if not usage:
            messagebox.showinfo("แจ้งเตือน", "ยังไม่มีข้อมูลให้ส่งออก")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")],
            initialfile=f"app_usage_{datetime.now():%Y%m%d_%H%M}.csv")
        if not path:
            return
        total = sum(usage.values())
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["ลำดับ", "แอปพลิเคชัน", "เวลาใช้งาน (ชช:นน:วว)", "วินาที", "สัดส่วน (%)"])
                ranked = sorted(usage.items(), key=lambda x: x[1], reverse=True)
                for i, (app, sec) in enumerate(ranked, 1):
                    pct = sec / total * 100 if total else 0
                    w.writerow([i, app, fmt_duration(sec), round(sec), round(pct, 2)])
                w.writerow([])
                w.writerow(["", "รวม", fmt_duration(total), round(total), 100])
            messagebox.showinfo("สำเร็จ", f"ส่งออกไฟล์แล้ว:\n{path}")
        except Exception as e:
            messagebox.showerror("ผิดพลาด", f"ส่งออกไม่สำเร็จ: {e}")

    def on_close(self):
        if self.tracker.is_running():
            if not messagebox.askyesno("ยืนยัน", "กำลังบันทึกอยู่ ถ้าปิดโปรแกรมจะหยุดบันทึก\n"
                                                 "ต้องการปิดไหม?"):
                return
            self.tracker.stop()
        self.save_settings()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()