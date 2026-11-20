"""Small helpers shared by the window and its dialogs (no Shadow state): open a file, the dark Windows title bar, the
narration kit loader. Split out of app.py (3.16); app.py imports these names back."""
import importlib, os, subprocess, sys

def open_path(p):
    os.startfile(p) if hasattr(os, 'startfile') else subprocess.Popen(['xdg-open', p])

def narrate_module():
    """make_narrated_scene, from tools/narrate next to app.py or inside the PyInstaller bundle (_MEIPASS)."""
    base = os.path.join(getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__))), 'tools', 'narrate')
    if base not in sys.path: sys.path.insert(0, base)
    return importlib.import_module('make_narrated_scene')

def dark_titlebar(win, dark):
    """Windows 10 1809+/11: DWMWA_USE_IMMERSIVE_DARK_MODE (20, or 19 on old builds). On Windows 11 the caption colour is also
    set (DWMWA_CAPTION_COLOR / TEXT_COLOR), otherwise "accent colour on title bars" keeps the bar coloured."""
    if os.name != 'nt': return
    try:
        import ctypes
        win.update_idletasks()
        dwm = ctypes.windll.dwmapi; hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        def put(attr, v):
            v = ctypes.c_uint(v); return dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))
        if put(20, 1 if dark else 0) != 0: put(19, 1 if dark else 0)
        DEFAULT = 0xFFFFFFFF  # DWMWA_COLOR_DEFAULT
        put(35, 0x001C1C1C if dark else DEFAULT)   # caption colour, COLORREF 0x00BBGGRR
        put(36, 0x00FFFFFF if dark else DEFAULT)   # caption text
        # repaint the non-client area now (SWP_NOMOVE|NOSIZE|NOZORDER|NOACTIVATE|FRAMECHANGED)
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)
    except Exception:
        pass
