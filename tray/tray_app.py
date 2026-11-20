"""TaskPilot AI system-tray launcher.

- Reuses a TaskPilot backend that is already running on 127.0.0.1:8000
  (for example one started by deploy.ps1 / run.ps1) instead of starting a
  second copy that cannot bind the port.
- If port 8000 is held by some other program, it says so once and does not
  keep retrying in a loop.
- In the packaged .exe, uvicorn runs inside this process on a thread
  (sys.executable is TaskPilotAI.exe there, so "-m uvicorn" cannot work).
  When run from source, the backend runs as a child Python process.
"""
import sys, os, time, json, socket, threading, subprocess, webbrowser, logging
import urllib.request

FROZEN = getattr(sys, 'frozen', False)

if FROZEN:
    BASE_DIR = sys._MEIPASS                         # bundled files (_internal)
    APP_DIR = os.path.dirname(sys.executable)       # install folder, for logs
    BACKEND_DIR = os.path.join(BASE_DIR, 'backend')
    ICON_PATH = os.path.join(BASE_DIR, 'tray', 'icon.ico')
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))   # tray/
    APP_DIR = os.path.dirname(BASE_DIR)                     # project root
    BACKEND_DIR = os.path.join(APP_DIR, 'backend')
    ICON_PATH = os.path.join(BASE_DIR, 'icon.ico')

HOST, PORT = '127.0.0.1', 8000
DASHBOARD_URL = f'http://{HOST}:{PORT}'
HEALTH_URL = f'{DASHBOARD_URL}/api/health'
LOG_PATH = os.path.join(APP_DIR, 'taskpilot-tray.log')
BACKEND_LOG = os.path.join(APP_DIR, 'backend.log')
STARTUP_GRACE = 60      # seconds to wait for a starting backend (sklearn import is slow)
WATCHDOG_EVERY = 10

logging.basicConfig(filename=LOG_PATH, level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')

# A windowed (console=False) exe has no stdout/stderr; anything that writes
# to them (uvicorn, warnings) would crash. Send it to backend.log instead.
if FROZEN and (sys.stdout is None or sys.stderr is None):
    _log_stream = open(BACKEND_LOG, 'a', buffering=1, encoding='utf-8')
    sys.stdout = sys.stdout or _log_stream
    sys.stderr = sys.stderr or _log_stream

_lock = threading.Lock()
_stop_event = threading.Event()
_icon = None
_owned = None            # the backend this tray started (thread or Popen), or None
_started_at = 0.0
_warned_port_busy = False


# ---------- checks ----------
def backend_healthy(timeout=2.0):
    """True if a TaskPilot backend answers /api/health on our port."""
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8')).get('status') == 'ok'
    except Exception:
        return False


def port_in_use():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((HOST, PORT)) == 0


def notify(msg):
    logging.info('Notify: %s', msg)
    try:
        if _icon is not None:
            _icon.notify(msg, 'TaskPilot AI')
    except Exception:
        pass


# ---------- backend runners ----------
class InProcessBackend:
    """Runs uvicorn on a background thread inside the packaged exe."""
    def __init__(self):
        import uvicorn
        if BACKEND_DIR not in sys.path:
            sys.path.insert(0, BACKEND_DIR)
        import main as backend_main          # backend/main.py bundled as data
        config = uvicorn.Config(
            backend_main.app, host=HOST, port=PORT,
            log_config=None, access_log=False,   # log through the tray logger
            loop='asyncio', http='h11', ws='auto',
        )
        self.server = uvicorn.Server(config)
        # Signal handlers can only be installed on the main thread.
        self.server.install_signal_handlers = lambda: None
        self.thread = threading.Thread(target=self._run, name='uvicorn', daemon=True)
        self.thread.start()

    def _run(self):
        try:
            self.server.run()
        except BaseException:
            logging.exception('Backend thread crashed')

    def alive(self):
        return self.thread.is_alive()

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=10)


class SubprocessBackend:
    """Runs `python -m uvicorn main:app` when the tray runs from source."""
    def __init__(self):
        cmd = [sys.executable, '-m', 'uvicorn', 'main:app',
               '--host', HOST, '--port', str(PORT), '--no-access-log']
        self._log = open(BACKEND_LOG, 'a', encoding='utf-8')
        self.proc = subprocess.Popen(
            cmd, cwd=BACKEND_DIR, stdout=subprocess.DEVNULL, stderr=self._log,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
        )

    def alive(self):
        return self.proc.poll() is None

    def stop(self):
        if self.alive():
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self._log.close()


# ---------- control ----------
def start_backend():
    """Start a backend only if none is reachable and the port is free."""
    global _owned, _started_at, _warned_port_busy
    with _lock:
        if _owned is not None and _owned.alive():
            return
        if backend_healthy():
            logging.info('TaskPilot backend already running on port %s - reusing it.', PORT)
            _warned_port_busy = False
            return
        if port_in_use():
            if not _warned_port_busy:
                logging.error('Port %s is used by another program; not starting the backend.', PORT)
                notify(f'Port {PORT} is in use by another program. TaskPilot backend not started.')
                _warned_port_busy = True
            return
        _warned_port_busy = False
        logging.info('Starting backend (%s)', 'in-process' if FROZEN else 'subprocess')
        try:
            _owned = InProcessBackend() if FROZEN else SubprocessBackend()
            _started_at = time.time()
        except Exception:
            logging.exception('Could not start backend')
            _owned = None


def stop_backend():
    global _owned
    with _lock:
        if _owned is not None:
            logging.info('Stopping backend')
            try:
                _owned.stop()
            except Exception:
                logging.exception('Error while stopping backend')
        _owned = None


def _watchdog():
    while not _stop_event.wait(WATCHDOG_EVERY):
        if backend_healthy():
            continue
        owned = _owned
        if owned is not None and owned.alive() and time.time() - _started_at < STARTUP_GRACE:
            continue                                   # still starting up
        if owned is not None and not owned.alive():
            logging.warning('Backend stopped unexpectedly - restarting.')
        start_backend()


# ---------- tray ----------
def _build_icon_image():
    from PIL import Image
    if os.path.exists(ICON_PATH):
        return Image.open(ICON_PATH).resize((64, 64))
    return Image.new('RGB', (64, 64), color=(30, 144, 255))


def _open_dashboard(icon, item):
    webbrowser.open_new_tab(DASHBOARD_URL)


def _restart_backend(icon, item):
    if _owned is None and backend_healthy():
        notify('The backend was started outside the tray (deploy.ps1 / run.ps1). '
               'Stop it with ".\\deploy.ps1 -Stop" to let the tray manage it.')
        return
    notify('Restarting backend...')
    stop_backend()
    for _ in range(20):                    # wait for the port to be released
        if not port_in_use():
            break
        time.sleep(0.5)
    start_backend()
    notify('Backend restarted.')


def _quit_app(icon, item):
    _stop_event.set()
    stop_backend()
    icon.stop()


def run_tray():
    global _icon
    try:
        import pystray
        from pystray import MenuItem as Item
    except ImportError:
        logging.warning('pystray not installed - running without a tray icon.')
        _stop_event.wait()
        return
    menu = pystray.Menu(
        Item('Open Dashboard', _open_dashboard, default=True),
        pystray.Menu.SEPARATOR,
        Item('Restart Backend', _restart_backend),
        pystray.Menu.SEPARATOR,
        Item('Quit TaskPilot AI', _quit_app),
    )
    _icon = pystray.Icon('TaskPilot AI', _build_icon_image(), 'TaskPilot AI', menu)
    _icon.run()


def main():
    logging.info('TaskPilot tray starting (frozen=%s)', FROZEN)
    start_backend()
    threading.Thread(target=_watchdog, daemon=True).start()
    run_tray()


if __name__ == '__main__':
    main()
