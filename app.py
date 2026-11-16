import ctypes
from ctypes import wintypes
from threading import Thread
import tkinter as tk
from tkinter import font, ttk

import cv2
import mss
import numpy as np

from feed import FeedServer
from pipeline import CaptureWorker, Gate, STREAM_URL


BACKGROUND = "#0A0B0F"
PANEL = "#0E1116"
FIELD = "#141820"
BORDER = "#2A3240"
TEXT = "#F2F5FB"
MUTED = "#A5AEBE"
MINT = "#3ADB92"
RED = "#FF8C9D"


def draw_shield(canvas, x, y, size, fill, mark):
    points = ((0, -0.5), (0.45, -0.31), (0.42, 0.03), (0.28, 0.28),
              (0, 0.49), (-0.28, 0.28), (-0.42, 0.03), (-0.45, -0.31))
    canvas.create_polygon([(x + dx * size, y + dy * size) for dx, dy in points], fill=fill, outline="")
    canvas.create_line(x - 0.18 * size, y, x - 0.06 * size, y + 0.12 * size,
                       x + 0.20 * size, y - 0.16 * size, fill=mark, width=max(2, size / 12))


def surface_texture(root, fill, border, background=BACKGROUND):
    scale, size, radius = 4, 32, 11
    shape = np.empty((size * scale, size * scale, 3), dtype=np.uint8)
    shape[:] = tuple(reversed(bytes.fromhex(background[1:])))

    def paint(color, pad):
        bgr = tuple(reversed(bytes.fromhex(color[1:])))
        end = size * scale - pad - 1
        curve = radius * scale - pad
        cv2.rectangle(shape, (pad + curve, pad), (end - curve, end), bgr, -1)
        cv2.rectangle(shape, (pad, pad + curve), (end, end - curve), bgr, -1)
        for x in (pad + curve, end - curve):
            for y in (pad + curve, end - curve):
                cv2.circle(shape, (x, y), curve, bgr, -1)

    paint(border, 0)
    paint(fill, scale)
    pixels = cv2.resize(shape, (size, size), interpolation=cv2.INTER_AREA)
    _, png = cv2.imencode(".png", pixels)
    return tk.PhotoImage(master=root, data=png.tobytes())


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


class OutputIllustration(tk.Canvas):
    def __init__(self, parent):
        super().__init__(parent, width=320, height=142, bg=PANEL, highlightthickness=0, takefocus=False)
        self.color = MUTED
        self.gradient = None
        self.gradient_size = None
        self.bind("<Configure>", self.draw)

    def update_output(self, color):
        if color != self.color:
            self.color = color
            self.draw()

    def draw(self, _event=None):
        self.delete("all")
        width, height = self.winfo_width(), self.winfo_height()
        if width < 80 or height < 80:
            return
        if (width, height) != self.gradient_size:
            self.gradient_size = width, height
            y, x = np.ogrid[:height, :width]
            strength = np.maximum(0, 1 - ((x - width * 0.5) / (width * 0.49)) ** 2
                                  - ((y - height * 0.52) / (height * 0.52)) ** 2) ** 2
            base = np.array((22, 17, 14))
            glow = np.array((10, 29, 6))
            _, png = cv2.imencode(".png", (base + strength[:, :, None] * glow).astype(np.uint8))
            self.gradient = tk.PhotoImage(master=self, data=png.tobytes())
        self.create_image(0, 0, image=self.gradient, anchor="nw")

        cx, cy, radius = width / 2, height / 2, 35
        ring = (cx - radius, cy - radius, cx + radius, cy + radius)
        self.create_oval(*ring, outline="#29443B", fill="#101C19")
        self.create_arc(*ring, start=264, extent=68, style="arc", outline=self.color, width=3)
        draw_shield(self, cx, cy, 28, self.color, "#101C19")


class App:
    def __init__(self, root):
        self.root = root
        self.gate = Gate()
        self.capture = CaptureWorker(self.gate)
        self.feed = FeedServer(self.gate)
        self.panic = PanicKey(self.gate)
        self.display_error = ""
        self.monitors = {}
        self.has_armed = False
        self.guide = None
        self.copy_timer = None
        self.last_status = None

        root.title("Stream Failsafe")
        root.configure(background=BACKGROUND)
        root.protocol("WM_DELETE_WINDOW", self.close)
        families = set(font.families(root))
        self.sans = "Inter" if "Inter" in families else "Segoe UI"
        self.mono = "IBM Plex Mono" if "IBM Plex Mono" in families else "Consolas"
        self.configure_styles()

        shell = tk.Frame(root, bg=BACKGROUND, padx=32, pady=22)
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        self.build_header(shell)
        body = tk.Frame(shell, bg=BACKGROUND)
        body.grid(row=1, column=0, sticky="ew", pady=(28, 12))
        body.columnconfigure(0, weight=1, minsize=430)
        body.columnconfigure(1, weight=1, minsize=390)
        controls = tk.Frame(body, bg=BACKGROUND)
        controls.grid(row=0, column=0, sticky="ew", padx=(0, 32))
        controls.columnconfigure(0, weight=1)
        self.build_status(controls)
        self.build_capture_controls(controls)
        self.build_source_controls(body)
        self.refresh_displays()
        for operation in (self.feed.run, self.capture.run, self.panic.run):
            Thread(target=operation, daemon=True).start()
        self.refresh_status()
        root.update_idletasks()
        root.minsize(root.winfo_reqwidth(), root.winfo_reqheight())

    def configure_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        self.textures = []
        for name, fill, border, foreground, hover, focus, backdrop in (
            ("Control", "#11151C", BORDER, TEXT, "#1A222D", "#6C7E94", BACKGROUND),
            ("Primary", MINT, MINT, "#071C14", "#6BE8AE", "#DDFFF0", BACKGROUND),
            ("Danger", "#301A22", "#8D3A4E", RED, "#462431", "#FFC3CF", BACKGROUND),
            ("Copy", "#11151C", BORDER, TEXT, "#1A222D", "#6C7E94", FIELD),
            ("Guide", MINT, MINT, "#071C14", "#6BE8AE", "#DDFFF0", PANEL),
        ):
            normal = surface_texture(self.root, fill, border, backdrop)
            active = surface_texture(self.root, hover, border, backdrop)
            focused = surface_texture(self.root, fill, focus, backdrop)
            disabled = surface_texture(self.root, "#14171D", "#222733", backdrop)
            self.textures.extend((normal, active, focused, disabled))
            variant = f"{name}.TButton"
            element = f"{name}.surface"
            style.element_create(element, "image", normal, ("disabled", disabled),
                                 ("focus", focused), ("active", active), border=13, sticky="nsew")
            style.layout(variant, [(element, {"sticky": "nsew", "children": [
                ("Button.padding", {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]})
            ]})])
            style.configure(variant, foreground=foreground, padding=(12, 2), font=(self.sans, 10, "bold"))
            style.map(variant, foreground=[("disabled", "#788190")])

        surface = surface_texture(self.root, PANEL, BORDER)
        self.textures.append(surface)
        style.element_create("Status.surface", "image", surface, border=13, sticky="nsew")
        style.layout("Status.TFrame", [("Status.surface", {"sticky": "nsew"})])
        style.configure("Display.TCombobox", foreground=TEXT, fieldbackground=FIELD,
                        background=FIELD, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                        arrowcolor=TEXT, arrowsize=13, padding=(10, 8), font=(self.sans, 11))
        style.map("Display.TCombobox", fieldbackground=[("readonly", FIELD), ("disabled", PANEL)],
                  foreground=[("disabled", MUTED)], bordercolor=[("focus", MINT)])
        self.root.option_add("*TCombobox*Listbox.background", FIELD)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", "#244838")
        self.root.option_add("*TCombobox*Listbox.font", (self.sans, 11))

    def label(self, parent, text="", *, bg=PANEL, fg=TEXT, size=11, weight="normal", family=None, **options):
        return tk.Label(parent, text=text, background=bg, foreground=fg, font=(family or self.sans, size, weight),
                        anchor="w", justify="left", padx=0, pady=0, **options)

    def build_header(self, parent):
        top = tk.Frame(parent, bg=BACKGROUND)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)
        mark = tk.Canvas(top, width=27, height=28, bg=BACKGROUND, highlightthickness=0, takefocus=False)
        mark.grid(row=0, column=0, padx=(0, 8))
        draw_shield(mark, 13, 14, 22, MINT, BACKGROUND)
        self.label(top, "Stream Failsafe", bg=BACKGROUND, size=15, weight="bold").grid(row=0, column=1, sticky="w")
        ttk.Button(top, text="OBS setup", style="Control.TButton", command=self.show_guide).grid(row=0, column=2)
        tk.Frame(top, bg="#20252F", height=1).grid(row=1, column=0, columnspan=3, sticky="ew", pady=(15, 0))

    def build_status(self, parent):
        panel = tk.Frame(parent, bg=BACKGROUND)
        panel.grid(row=0, column=0, sticky="ew", pady=(0, 18))
        self.label(panel, "TWITCH  /  TIKTOK LIVE", bg=BACKGROUND, fg=MINT, size=10,
                   family=self.mono, weight="bold").pack(anchor="w", pady=(0, 13))
        self.status = self.label(panel, "Starting", bg=BACKGROUND, size=39, weight="bold")
        self.status.pack(anchor="w")
        self.status_accent = self.label(panel, "up", bg=BACKGROUND, fg=MINT, size=39, weight="bold")
        self.status_accent.pack(anchor="w")
        self.reason = self.label(panel, "Loading the screen detector.", bg=BACKGROUND, fg=MUTED,
                                 size=12, wraplength=385)
        self.reason.pack(anchor="w", pady=(10, 0))

    def build_capture_controls(self, parent):
        panel = tk.Frame(parent, bg=BACKGROUND)
        panel.grid(row=1, column=0, sticky="ew")
        panel.columnconfigure(0, weight=1)
        self.label(panel, "Capture display", bg=BACKGROUND, weight="bold").grid(row=0, column=0, sticky="w", pady=(0, 7))

        display_row = tk.Frame(panel, bg=BACKGROUND)
        display_row.grid(row=1, column=0, sticky="ew")
        display_row.columnconfigure(0, weight=1)
        self.display = ttk.Combobox(display_row, state="readonly", width=28, style="Display.TCombobox")
        self.display.configure(font=(self.sans, 11))
        self.display.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.refresh_button = ttk.Button(display_row, text="Refresh", style="Control.TButton", command=self.refresh_displays)
        self.refresh_button.grid(row=0, column=1, sticky="ns")

        actions = tk.Frame(panel, bg=BACKGROUND)
        actions.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        actions.columnconfigure(0, weight=1)
        self.action_button = ttk.Button(actions, text="Arm protection", style="Primary.TButton", command=self.arm)
        self.action_button.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.test_button = ttk.Button(actions, text="Test blackout", style="Control.TButton", command=self.test_blackout)
        self.test_button.grid(row=0, column=1, sticky="ns")

        self.hotkey_hint = self.label(panel, "Panic shortcut  Ctrl + Shift + F10", bg=BACKGROUND, fg=MUTED,
                                      size=10, family=self.mono)
        self.hotkey_hint.grid(row=3, column=0, sticky="w", pady=(12, 0))

    def build_source_controls(self, parent):
        route = ttk.Frame(parent, padding=(16, 14), style="Status.TFrame")
        route.grid(row=0, column=1, sticky="ew")
        route.columnconfigure(0, weight=1)
        self.label(route, "OBS Browser Source", weight="bold").grid(row=0, sticky="w", pady=(2, 10))
        address = tk.Frame(route, bg=FIELD, padx=9, pady=4, highlightthickness=1, highlightbackground="#222936")
        address.grid(row=1, sticky="ew")
        address.columnconfigure(0, weight=1)
        self.label(address, STREAM_URL, bg=FIELD, fg=MUTED, size=10, family=self.mono).grid(row=0, column=0, sticky="w")
        self.copy_button = ttk.Button(address, text="Copy URL", width=9, style="Copy.TButton", command=self.copy_url)
        self.copy_button.grid(row=0, column=1)

        self.illustration = OutputIllustration(route)
        self.illustration.grid(row=2, sticky="ew", pady=(1, 0))
        self.output_tag = self.label(route, "Output is black", size=15, weight="bold")
        self.output_tag.grid(row=3)
        self.metrics = self.label(route, " ", fg=MINT, size=10, family=self.mono)
        self.metrics.grid(row=4, pady=(4, 11))
        tk.Frame(route, bg="#202632", height=1).grid(row=5, sticky="ew", pady=(0, 10))
        self.connection = self.label(route, "Waiting for OBS", fg=MUTED, size=10)
        self.connection.grid(row=6, padx=4, pady=(0, 2))

    def refresh_displays(self):
        previous = self.display.get()
        try:
            with mss.mss() as capture:
                monitors = capture.monitors[1:]
            self.monitors = {}
            for index, monitor in enumerate(monitors, 1):
                primary = monitor["left"] == monitor["top"] == 0
                size = f'{monitor["width"]} × {monitor["height"]}'
                display_name = "Main display" if primary else f"Display {index}"
                name = f"{display_name}  ·  {size}"
                self.monitors[name] = monitor
            self.display.configure(values=list(self.monitors))
            first = next(iter(self.monitors), "")
            main = next((name for name in self.monitors if name.startswith("Main display")), first)
            self.display.set(previous if previous in self.monitors else main)
            self.display_error = "" if self.monitors else "Windows did not report any displays."
        except Exception as exc:
            self.monitors = {}
            self.display.configure(values=())
            self.display.set("")
            self.display_error = f"Could not list displays. {exc}"

    def refresh_status(self):
        mode, reason, _, processing_ms, fps = self.gate.snapshot()
        armed = mode != "Blackout"
        ready = self.capture.ready.is_set() and self.feed.ready.is_set()
        output_on = mode == "Armed"
        connected = self.feed.connected
        metrics = f"{processing_ms:.0f} ms per frame  /  {fps:.0f} fps" if output_on else " "
        self.metrics.configure(text=metrics)

        error = self.feed.error or self.capture.error or self.display_error
        state = (mode, reason, ready, connected, error, self.panic.available, bool(self.monitors))
        if state != self.last_status:
            self.last_status = state
            if error:
                title, reason, color = "Setup needs\nattention", error, RED
            elif not ready:
                title, reason, color = "Starting\nup", "Loading the local screen detector.", MINT
            elif output_on:
                title, reason, color = "Protection\nactive", "Only screened frames are sent to the Browser Source.", MINT
            elif armed:
                title, reason, color = "Checking\ndisplay", "Output stays black while the first frames are checked.", MINT
            elif self.has_armed:
                title, color = "Blackout\nactive", RED
            else:
                title, reason, color = "Ready to\narm", "Choose what to share, then arm protection.", MINT

            headline, accent = title.split("\n")
            self.status.configure(text=headline)
            self.status_accent.configure(text=accent, fg=color)
            self.reason.configure(text=reason)
            self.output_tag.configure(text="Screened output" if output_on else "Output is black")
            self.illustration.update_output(color)
            self.connection.configure(text="Connected" if connected else "Waiting for OBS", fg=MINT if connected else MUTED)
            self.action_button.configure(text="Black out" if armed else "Arm protection",
                                         style="Danger.TButton" if armed else "Primary.TButton",
                                         command=self.blackout if armed else self.arm,
                                         state="normal" if armed or (ready and self.monitors) else "disabled")
            self.test_button.configure(state="normal" if output_on else "disabled")
            self.display.configure(state="disabled" if armed else "readonly")
            self.refresh_button.configure(state="disabled" if armed else "normal")
            self.hotkey_hint.configure(text="Panic shortcut unavailable. Use Black out." if self.panic.available is False
                                      else "Panic shortcut  Ctrl + Shift + F10")
        self.root.after(75, self.refresh_status)

    def arm(self):
        monitor = self.monitors.get(self.display.get())
        if self.gate.snapshot()[0] == "Blackout" and monitor and self.capture.ready.is_set() and self.feed.ready.is_set():
            self.has_armed = True
            self.gate.arm(monitor)

    def blackout(self):
        self.gate.blackout("Manual blackout. Re-arm when ready.")

    def test_blackout(self):
        self.gate.blackout("Test complete. Check that OBS shows black, then re-arm.")

    def copy_url(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(STREAM_URL)
        self.copy_button.configure(text="Copied")
        if self.guide is not None:
            self.guide_copy.configure(text="Copied")
        if self.copy_timer is not None:
            self.root.after_cancel(self.copy_timer)
        self.copy_timer = self.root.after(2000, self.reset_copy_labels)

    def reset_copy_labels(self):
        self.copy_timer = None
        self.copy_button.configure(text="Copy URL")
        if self.guide is not None:
            self.guide_copy.configure(text="Copy URL")

    def show_guide(self):
        if self.guide is not None:
            self.guide.lift()
            self.guide.focus_set()
            return

        self.guide = guide = tk.Toplevel(self.root, bg=BACKGROUND)
        guide.title("OBS setup")
        guide.transient(self.root)
        guide.resizable(False, False)
        guide.protocol("WM_DELETE_WINDOW", self.close_guide)
        guide.bind("<Escape>", self.close_guide)
        body = tk.Frame(guide, bg=BACKGROUND, padx=23, pady=20)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)

        self.label(body, "Connect Stream Failsafe to OBS", bg=BACKGROUND, size=18, weight="bold").grid(row=0, sticky="w")
        url_row = tk.Frame(body, bg=PANEL, padx=11, pady=8, highlightthickness=1, highlightbackground=BORDER)
        url_row.grid(row=1, sticky="ew", pady=(13, 15))
        url_row.columnconfigure(0, weight=1)
        self.label(url_row, STREAM_URL, fg=MINT, family=self.mono, size=11).grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.guide_copy = ttk.Button(url_row, text="Copy URL", width=10, style="Guide.TButton", command=self.copy_url)
        self.guide_copy.grid(row=0, column=1)

        steps = (
            ("1. Add a Browser Source", "Open OBS and create a new scene. Add a Browser Source, paste the URL, and match its width and height to your OBS canvas."),
            ("2. Route the display through this source", "Put a black Color Source underneath it. Remove any direct display, window, or game capture of that content from the scene."),
            ("3. Arm and test", "Choose your display and click Arm protection. Click Test blackout and confirm the OBS preview turns black. Re-arm when ready."),
        )
        for row, (title, detail) in enumerate(steps, 2):
            step = tk.Frame(body, bg=BACKGROUND)
            step.grid(row=row, sticky="ew", pady=(0, 11))
            self.label(step, title, bg=BACKGROUND, size=11, weight="bold").pack(anchor="w")
            self.label(step, detail, bg=BACKGROUND, fg=MUTED, wraplength=440).pack(anchor="w", pady=(4, 0))

        self.label(body, "Use this OBS scene for Twitch. For TikTok LIVE, select OBS Virtual Camera if available.",
                   bg=BACKGROUND, fg=MUTED, size=10, wraplength=440).grid(row=5, sticky="w", pady=(2, 11))
        ttk.Button(body, text="Done", style="Control.TButton", command=self.close_guide).grid(row=6, sticky="e")
        guide.update_idletasks()
        x = self.root.winfo_x() + max(0, (self.root.winfo_width() - guide.winfo_reqwidth()) // 2)
        y = self.root.winfo_y() + 36
        guide.geometry(f"+{x}+{y}")
        self.guide_copy.focus_set()

    def close_guide(self, _event=None):
        self.guide.destroy()
        self.guide = None

    def close(self):
        self.gate.blackout("Stream Failsafe closed.")
        self.root.destroy()


if __name__ == "__main__":
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    root = tk.Tk()
    App(root)
    root.mainloop()
