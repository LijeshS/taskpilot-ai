import sys, os, time, threading, subprocess, webbrowser, logging

if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
    APP_DIR  = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    APP_DIR  = os.path.dirname(BASE_DIR)

BACKEND_DIR   = os.path.join(APP_DIR, 'backend')
ICON_PATH     = os.path.join(BASE_DIR, 'icon.ico')
DASHBOARD_URL = 'http://127.0.0.1:8000'
LOG_PATH      = os.path.join(APP_DIR, 'taskpilot-tray.log')

logging.basicConfig(filename=LOG_PATH, level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')

_backend_proc = None
_stop_event   = threading.Event()

def start_backend():
    global _backend_proc
    if _backend_proc and _backend_proc.poll() is None:
        return
    if getattr(sys, 'frozen', False):
        cmd = [sys.executable, '-m', 'uvicorn', 'backend.main:app',
               '--host', '127.0.0.1', '--port', '8000', '--no-access-log']
        cwd = APP_DIR
    else:
        cmd = [sys.executable, '-m', 'uvicorn', 'main:app',
               '--host', '127.0.0.1', '--port', '8000', '--no-access-log']
        cwd = BACKEND_DIR
    logging.info('Starting backend')
    _backend_proc = subprocess.Popen(
        cmd, cwd=cwd,
        stdout=subprocess.DEVNULL,
        stderr=open(os.path.join(APP_DIR, 'backend.log'), 'a'),
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
    )

def stop_backend():
    global _backend_proc
    if _backend_proc and _backend_proc.poll() is None:
        _backend_proc.terminate()
        try: _backend_proc.wait(timeout=5)
        except subprocess.TimeoutExpired: _backend_proc.kill()
    _backend_proc = None

def _watchdog():
    while not _stop_event.is_set():
        time.sleep(10)
        if _backend_proc is None or _backend_proc.poll() is not None:
            logging.warning('Backend not running - restarting.')
            start_backend()

def _build_icon_image():
    from PIL import Image
    if os.path.exists(ICON_PATH):
        return Image.open(ICON_PATH).resize((64, 64))
    return Image.new('RGB', (64, 64), color=(30, 144, 255))

def _open_dashboard(icon, item):
    webbrowser.open_new_tab(DASHBOARD_URL)

def _restart_backend(icon, item):
    icon.notify('Restarting backend...', 'TaskPilot AI')
    stop_backend()
    time.sleep(1)
    start_backend()
    icon.notify('Backend restarted.', 'TaskPilot AI')

def _quit_app(icon, item):
    _stop_event.set()
    stop_backend()
    icon.stop()

def run_tray():
    try:
        import pystray
        from pystray import MenuItem as Item, Menu
    except ImportError:
        _stop_event.wait()
        return
    image = _build_icon_image()
    menu  = pystray.Menu(
        Item('Open Dashboard',    _open_dashboard, default=True),
        pystray.Menu.SEPARATOR,
        Item('Restart Backend',   _restart_backend),
        pystray.Menu.SEPARATOR,
        Item('Quit TaskPilot AI', _quit_app),
    )
    icon = pystray.Icon('TaskPilot AI', image, 'TaskPilot AI', menu)
    icon.run()

def main():
    start_backend()
    time.sleep(2)
    threading.Thread(target=_watchdog, daemon=True).start()
    run_tray()

if __name__ == '__main__':
    main()
