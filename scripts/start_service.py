"""Run the web server and billing worker as one supervised Railway service."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time


ROOT = Path(__file__).resolve().parents[1]


def stop_children(children, grace_seconds=15):
    for _, child in children:
        if child.poll() is None:
            try:
                if os.name == 'posix':
                    os.killpg(child.pid, signal.SIGTERM)
                else:
                    child.terminate()
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + grace_seconds
    for _, child in children:
        try:
            child.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            try:
                if os.name == 'posix':
                    os.killpg(child.pid, signal.SIGKILL)
                else:
                    child.kill()
            except ProcessLookupError:
                pass
            child.wait()


def supervise(commands, stopping):
    children = []
    try:
        for name, command in commands:
            if stopping.is_set():
                return 0
            child = subprocess.Popen(command, cwd=ROOT, start_new_session=os.name == 'posix')
            children.append((name, child))
            print(f'Started {name} (pid {child.pid}).', flush=True)
        while not stopping.wait(0.2):
            for name, child in children:
                code = child.poll()
                if code is not None:
                    print(f'{name} exited (code {code}); stopping service for restart.', flush=True)
                    return 1
        return 0
    finally:
        stop_children(children)


def main():
    port = int(os.getenv('PORT', '8080'))
    if not 1 <= port <= 65535:
        raise ValueError('PORT must be between 1 and 65535')
    stopping = threading.Event()
    for name in ('SIGTERM', 'SIGINT'):
        signal.signal(getattr(signal, name), lambda *_: stopping.set())
    checked = subprocess.run([sys.executable, '-m', 'flask', '--app', 'app', 'app-check'], cwd=ROOT)
    if checked.returncode:
        return 1
    return supervise([
        ('billing worker', [sys.executable, '-m', 'flask', '--app', 'app', 'billing-worker']),
        ('web server', [sys.executable, '-m', 'gunicorn', '--bind', f'0.0.0.0:{port}',
                        '--workers', '1', '--graceful-timeout', '10', 'app:app']),
    ], stopping)


if __name__ == '__main__':
    sys.exit(main())
