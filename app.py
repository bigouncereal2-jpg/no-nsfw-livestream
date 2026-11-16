import ctypes
from ctypes import wintypes
from threading import Thread
import tkinter as tk
from tkinter import ttk

import mss

from feed import FeedServer
from pipeline import CaptureWorker, Gate, STREAM_URL


class PanicKey:
    def __init__(self, gate):
        self.gate = gate
        self.available = None

    def run(self):
        user32 = ctypes.windll.user32
        hotkey = 1
        ctrl_shift_no_repeat = 0x0002 | 0x0004 | 0x4000
        f10 = 0x79
        self.available = bool(user32.RegisterHotKey(None, hotkey, ctrl_shift_no_repeat, f10))
        if not self.available:
            return

        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            if message.message == 0x0312 and message.wParam == hotkey:
                self.gate.blackout("Panic key pressed. Re-arm when ready.")
        user32.UnregisterHotKey(None, hotkey)


class App:
    def __init__(self, root):
        self.root = root
        self.gate = Gate()
        self.capture = CaptureWorker(self.gate)
        self.feed = FeedServer(self.gate)
        self.panic = PanicKey(self.gate)
        self.display_error = ""
        self.monitors = {}

        root.title("Stream Failsafe")
        root.resizable(False, False)
        root.configure(background="#e6ebee")
        root.protocol("WM_DELETE_WINDOW", self.close)

        style = ttk.Style(root)
        style.configure("TButton", padding=(10, 5))
        style.configure("Shell.TFrame", background="#e6ebee")
        style.configure("Panel.TFrame", background="#f7f9fa")
        style.configure("Panel.TLabel", background="#f7f9fa", foreground="#23323c")
        style.configure("Hint.TLabel", background="#e6ebee", foreground="#344958")
        style.configure("Status.TLabel", background="#f7f9fa", font=("Inter", 22, "bold"))

        shell = ttk.Frame(root, padding=20, style="Shell.TFrame")
        shell.grid(sticky="nsew")
        panel = ttk.Frame(shell, padding=(16, 12), style="Panel.TFrame")
        panel.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        panel.columnconfigure(0, minsize=380)

        self.status = ttk.Label(panel, style="Status.TLabel")
        self.status.grid(row=0, column=0, sticky="w")
        self.reason = ttk.Label(panel, style="Panel.TLabel", wraplength=370)
        self.reason.grid(row=1, column=0, sticky="w", pady=(2, 6))
        self.metrics = ttk.Label(panel, style="Panel.TLabel")
        self.metrics.grid(row=2, column=0, sticky="w")

        controls = ttk.Frame(shell, style="Shell.TFrame")
        controls.grid(row=1, column=0, sticky="ew")
        controls.columnconfigure(1, weight=1)
        ttk.Label(controls, text="Display", style="Hint.TLabel").grid(row=0, column=0, sticky="w")
        self.display = ttk.Combobox(controls, state="readonly", width=26)
        self.display.grid(row=0, column=1, sticky="ew", padx=(10, 8))
        self.refresh_button = ttk.Button(controls, text="Refresh", command=self.refresh_displays)
        self.refresh_button.grid(row=0, column=2)

        actions = ttk.Frame(shell, style="Shell.TFrame")
        actions.grid(row=2, column=0, sticky="w", pady=(14, 0))
        self.arm_button = ttk.Button(actions, text="Arm", command=self.arm)
        self.arm_button.grid(row=0, column=0, padx=(0, 8))
        self.black_button = ttk.Button(actions, text="Black out", command=self.blackout)
        self.black_button.grid(row=0, column=1, padx=(0, 8))
        self.test_button = ttk.Button(actions, text="Test blackout", command=self.test_blackout)
        self.test_button.grid(row=0, column=2)

        self.hotkey_hint = ttk.Label(shell, style="Hint.TLabel")
        self.hotkey_hint.grid(row=3, column=0, sticky="w", pady=(6, 14))

        route = ttk.Frame(shell, style="Shell.TFrame")
        route.grid(row=4, column=0, sticky="ew")
        route.columnconfigure(0, weight=1)
        ttk.Label(route, text=STREAM_URL, style="Hint.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(route, text="Copy OBS URL", command=self.copy_url).grid(row=0, column=1)

        self.refresh_displays()
        for operation in (self.feed.run, self.capture.run, self.panic.run):
            Thread(target=operation, daemon=True).start()
        self.refresh_status()

    def refresh_displays(self):
        previous = self.display.get()
        try:
            with mss.mss() as capture:
                monitors = capture.monitors[1:]
            self.monitors = {}
            for index, monitor in enumerate(monitors, 1):
                primary = monitor["left"] == monitor["top"] == 0
                size = f'{monitor["width"]} × {monitor["height"]}'
                name = f"Display {index}  ·  {size}" + ("  ·  Main" if primary else "")
                self.monitors[name] = monitor
            self.display.configure(values=list(self.monitors))
            first = next(iter(self.monitors), "")
            main = next((name for name in self.monitors if name.endswith("Main")), first)
            self.display.set(previous if previous in self.monitors else main)
            self.display_error = "" if self.monitors else "Windows did not report any displays."
        except Exception as exc:
            self.monitors = {}
            self.display.set("")
            self.display_error = f"Could not list displays. {exc}"

    def refresh_status(self):
        mode, reason, _, processing_ms, fps = self.gate.snapshot()
        armed = mode != "Blackout"
        ready = self.capture.ready.is_set() and self.feed.ready.is_set()
        if self.feed.error:
            reason = f"The OBS source could not start. {self.feed.error}"
        elif self.capture.error:
            reason = f"The screen detector could not start. {self.capture.error}"
        elif self.display_error:
            reason = self.display_error
        elif not ready:
            reason = "Loading the local detector."

        color = {"Armed": "#145840", "Checking": "#705107", "Blackout": "#982f2f"}[mode]
        self.status.configure(text=mode, foreground=color)
        self.reason.configure(text=reason)
        metrics = f"{processing_ms:.0f} ms per frame  ·  {fps:.0f} fps" if mode == "Armed" else " "
        self.metrics.configure(text=metrics)
        self.arm_button.configure(state="normal" if ready and not armed and self.monitors else "disabled")
        self.black_button.configure(state="normal" if armed else "disabled")
        self.test_button.configure(state="normal" if mode == "Armed" else "disabled")
        self.display.configure(state="disabled" if armed else "readonly")
        self.refresh_button.configure(state="disabled" if armed else "normal")

        if self.panic.available is not None:
            text = "Panic key  Ctrl+Shift+F10" if self.panic.available else "Panic key unavailable. Use Black out."
            self.hotkey_hint.configure(text=text)
        self.root.after(75, self.refresh_status)

    def arm(self):
        monitor = self.monitors.get(self.display.get())
        if monitor and self.capture.ready.is_set() and self.feed.ready.is_set():
            self.gate.arm(monitor)

    def blackout(self):
        self.gate.blackout("Manual blackout. Re-arm when ready.")

    def test_blackout(self):
        self.gate.blackout("Test blackout. Re-arm when ready.")

    def copy_url(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(STREAM_URL)

    def close(self):
        self.gate.blackout("Stream Failsafe closed.")
        self.root.destroy()


if __name__ == "__main__":
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    root = tk.Tk()
    App(root)
    root.mainloop()
