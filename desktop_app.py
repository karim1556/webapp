import sys
import os
import time
import webbrowser
import threading
import socket
import logging
import subprocess
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if getattr(sys, 'frozen', False):
    BASE_DIR = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import config
import database as db
from app import create_app

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('PhantomLimbDesktop')
_instance_lock = None

def acquire_single_instance() -> bool:
    global _instance_lock
    lock_path = os.path.join(os.path.dirname(config.DB_PATH), 'PhantomLimb.lock')
    try:
        lock_file = open(lock_path, 'a+')
        if os.name == 'nt':
            import msvcrt
            lock_file.seek(0)
            lock_file.write('1')
            lock_file.flush()
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _instance_lock = lock_file
        return True
    except (ImportError, OSError, IOError):
        return True

def get_free_port(start_port: int = 58490) -> int:
    for port in range(start_port, start_port + 100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('127.0.0.1', port))
                return port
            except OSError:
                continue
    return start_port

def is_phantomlimb_responding(port: int) -> bool:
    try:
        req = urllib.request.Request(f'http://127.0.0.1:{port}/manifest.json', headers={'User-Agent': 'PhantomLimb-Ping'})
        with urllib.request.urlopen(req, timeout=1.0) as res:
            return res.status == 200
    except Exception:
        return False

def wait_for_server(port: int, max_seconds: float = 10.0) -> bool:
    start = time.time()
    while time.time() - start < max_seconds:
        try:
            req = urllib.request.Request(f'http://127.0.0.1:{port}/', headers={'User-Agent': 'PhantomLimb-Health'})
            with urllib.request.urlopen(req, timeout=1.0) as res:
                return True
        except Exception:
            time.sleep(0.15)
    return False

def run_server(app, port: int):
    app.run(host='127.0.0.1', port=port, debug=False, use_reloader=False)

def open_fallback_browser(url: str):
    for browser_app in ['Brave Browser', 'Google Chrome', 'Microsoft Edge']:
        if os.path.exists(f'/Applications/{browser_app}.app'):
            try:
                subprocess.Popen(['open', '-na', browser_app, '--args', f'--app={url}'])
                return
            except Exception:
                pass
    webbrowser.open(url)

def main():
    acquire_single_instance()
    
    preferred_port = 58490
    server_thread = None
    
    if is_phantomlimb_responding(preferred_port):
        port = preferred_port
    else:
        port = get_free_port(preferred_port)
        app = create_app()
        server_thread = threading.Thread(target=run_server, args=(app, port), daemon=True)
        server_thread.start()
        wait_for_server(port)

    url = f'http://127.0.0.1:{port}'
    try:
        import webview
        window = webview.create_window(
            'Neurosync AI — Wearable Monitor',
            url,
            width=1280,
            height=820,
            min_size=(960, 640),
            background_color='#F7F8F6'
        )
        webview.start()
    except Exception as e:
        logger.warning('Native webview unavailable: %s. Opening browser window.', e)
        open_fallback_browser(url)
        if server_thread:
            server_thread.join()

if __name__ == '__main__':
    main()
