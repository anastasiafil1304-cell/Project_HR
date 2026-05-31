import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_HEALTH_TEXT = 'Проверка не выполнялась'
DEFAULT_PUBLIC_URL_TEXT = 'Онлайн-ссылка не активна'
LOCAL_STARTING_TEXT = 'Сервер запускается...'
DOCKER_STARTING_TEXT = 'Docker-режим запускается...'
ONLINE_STARTING_TEXT = 'Онлайн-режим запускается...'
ONLINE_FAILED_TEXT = 'Ошибка запуска онлайн-режима'
ONLINE_VALIDATING_TEXT = 'Онлайн-ссылка проверяется...'
ONLINE_READY_WITH_WARNING_TEXT = 'Онлайн-ссылка получена'
ONLINE_START_TIMEOUT_SECONDS = 25
TRYCLOUDFLARE_URL_PATTERN = re.compile(r'https://[A-Za-z0-9.-]+trycloudflare\.com')
CLOUDFLARED_EXE_NAME = 'cloudflared.exe'
APP_PORT_OVERRIDE = None


def get_runtime_root():
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def env_file_path():
    return get_runtime_root() / '.env'


def env_example_path():
    return get_runtime_root() / '.env.example'


def ensure_env_file():
    env_path = env_file_path()
    if env_path.exists():
        return env_path

    example_path = env_example_path()
    if example_path.exists():
        shutil.copyfile(example_path, env_path)
    else:
        env_path.write_text('', encoding='utf-8')
    return env_path


def read_env_map():
    env_path = ensure_env_file()
    env_map = {}
    for raw_line in env_path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        env_map[key.strip()] = value.strip()
    return env_map


def update_env_values(updates):
    env_path = ensure_env_file()
    original_lines = env_path.read_text(encoding='utf-8').splitlines()
    remaining = dict(updates)
    new_lines = []

    for line in original_lines:
        stripped = line.strip()
        if '=' not in stripped or stripped.startswith('#'):
            new_lines.append(line)
            continue

        key, _ = stripped.split('=', 1)
        key = key.strip()
        if key in remaining:
            new_lines.append(f'{key}={remaining.pop(key)}')
        else:
            new_lines.append(line)

    for key, value in remaining.items():
        new_lines.append(f'{key}={value}')

    env_path.write_text('\n'.join(new_lines) + '\n', encoding='utf-8')


def get_setting(name, default):
    return read_env_map().get(name, default)


def runtime_relative_path(value):
    if not value:
        return ''
    path = Path(value)
    if not path.is_absolute():
        path = get_runtime_root() / path
    return str(path)


def default_ollama_models_path():
    path = get_runtime_root() / '.ollama' / 'models'
    return str(path) if path.exists() else ''


def server_command():
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--serve-local']
    return [sys.executable, str(Path(__file__).resolve()), '--serve-local']


def server_cwd():
    return str(get_runtime_root())


def server_log_path():
    return get_runtime_root() / 'server-start.log'


def reset_server_log():
    try:
        server_log_path().unlink(missing_ok=True)
    except Exception:
        return


def append_server_log(message):
    try:
        with server_log_path().open('a', encoding='utf-8') as log_file:
            log_file.write(message.rstrip() + '\n')
    except Exception:
        return


def read_server_log_tail(max_chars=1200):
    try:
        content = server_log_path().read_text(encoding='utf-8')
    except Exception:
        return ''
    if len(content) <= max_chars:
        return content.strip()
    return content[-max_chars:].strip()


def docker_command(*args):
    return ['docker', 'compose', *args]


def app_port():
    if APP_PORT_OVERRIDE:
        return APP_PORT_OVERRIDE
    env_map = read_env_map()
    return env_map.get('APP_PORT', '5000')


def set_app_port_override(port):
    global APP_PORT_OVERRIDE
    APP_PORT_OVERRIDE = str(port) if port else None


def local_access_host():
    env_map = read_env_map()
    host = env_map.get('APP_HOST', '127.0.0.1').strip() or '127.0.0.1'
    if host in {'0.0.0.0', '::', '[::]'}:
        return '127.0.0.1'
    return host


def tunnel_origin_url():
    return f'http://127.0.0.1:{app_port()}'


def existing_path(candidates):
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def env_candidate_dirs():
    candidates = []
    for env_name in ('ProgramFiles', 'ProgramFiles(x86)', 'ProgramW6432', 'LOCALAPPDATA'):
        env_value = os.getenv(env_name)
        if env_value:
            candidates.append(Path(env_value))
    return candidates


def cloudflared_install_candidates():
    runtime_root = get_runtime_root()
    candidates = [
        runtime_root / CLOUDFLARED_EXE_NAME,
        runtime_root / 'cloudflared',
    ]

    for base_dir in env_candidate_dirs():
        candidates.extend([
            base_dir / 'cloudflared' / CLOUDFLARED_EXE_NAME,
            base_dir / 'Programs' / 'cloudflared' / CLOUDFLARED_EXE_NAME,
        ])

    winget_root = os.getenv('LOCALAPPDATA')
    if winget_root:
        package_root = Path(winget_root) / 'Microsoft' / 'WinGet' / 'Packages'
        if package_root.exists():
            for package_dir in package_root.glob('Cloudflare.cloudflared_*'):
                candidates.append(package_dir / CLOUDFLARED_EXE_NAME)

    return candidates


def find_cloudflared_binary():
    path_binary = shutil.which('cloudflared')
    if path_binary:
        return path_binary
    return existing_path(cloudflared_install_candidates())


def tunnel_command():
    cloudflared_binary = find_cloudflared_binary()
    if not cloudflared_binary:
        return None
    return [cloudflared_binary, 'tunnel', '--url', tunnel_origin_url(), '--no-autoupdate']


def windows_listener_pids(port):
    output = subprocess.check_output(
        ['netstat', '-ano', '-p', 'tcp'],
        text=True,
        encoding='utf-8',
        errors='ignore',
    )
    pids = set()
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line.startswith('TCP'):
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        if parts[3].upper() != 'LISTENING':
            continue
        if parts[1].endswith(f':{port}') and parts[4].isdigit():
            pids.add(int(parts[4]))
    return sorted(pids)


def unix_listener_pids(port):
    output = subprocess.check_output(
        ['lsof', '-ti', f'TCP:{port}', '-sTCP:LISTEN'],
        text=True,
        encoding='utf-8',
        errors='ignore',
    )
    return sorted({int(line.strip()) for line in output.splitlines() if line.strip().isdigit()})


def port_listener_pids(port):
    try:
        if os.name == 'nt':
            return windows_listener_pids(port)

        return unix_listener_pids(port)
    except Exception:
        return []


def project_hr_health_url(port):
    return f'http://127.0.0.1:{port}/health'


def is_project_hr_server_on_port(port):
    try:
        with urlopen(project_hr_health_url(port), timeout=1.5) as response:
            payload = response.read().decode('utf-8', errors='ignore')
            return response.status == 200 and '"status"' in payload and '"database_engine"' in payload
    except Exception:
        return False


def find_free_local_port(start_port, attempts=20):
    port = int(start_port)
    for candidate in range(port, port + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(('127.0.0.1', candidate))
            except OSError:
                continue
            return candidate
    return None


def choose_runtime_port():
    preferred_port = int(get_setting('APP_PORT', '5000'))
    if not port_listener_pids(preferred_port):
        return preferred_port, None

    if is_project_hr_server_on_port(preferred_port):
        return preferred_port, None

    fallback_port = find_free_local_port(preferred_port + 1)
    if fallback_port is None:
        return preferred_port, f'Порт {preferred_port} занят и свободный порт не найден.'
    return fallback_port, f'Порт {preferred_port} занят другим приложением. Использую {fallback_port}.'


def terminate_pid(pid):
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(pid), '/F'], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        os.kill(pid, 15)
    except Exception:
        return


def process_name_pids(image_name):
    try:
        if os.name == 'nt':
            output = subprocess.check_output(
                ['tasklist', '/FI', f'IMAGENAME eq {image_name}', '/FO', 'CSV', '/NH'],
                text=True,
                encoding='utf-8',
                errors='ignore',
            )
            pids = []
            for line in output.splitlines():
                line = line.strip()
                if not line or 'Нет задач' in line or 'INFO:' in line:
                    continue
                parts = [part.strip('"') for part in line.split('","')]
                if len(parts) >= 2 and parts[1].isdigit():
                    pids.append(int(parts[1]))
            return sorted(set(pids))

        output = subprocess.check_output(['pgrep', '-f', image_name], text=True, encoding='utf-8', errors='ignore')
        return sorted({int(line.strip()) for line in output.splitlines() if line.strip().isdigit()})
    except Exception:
        return []


def cleanup_app_port_listeners():
    current_pid = os.getpid()
    for pid in port_listener_pids(app_port()):
        if pid != current_pid:
            terminate_pid(pid)


def cleanup_cloudflared_processes():
    current_pid = os.getpid()
    for image_name in ('cloudflared.exe', 'cloudflared.EXE', 'cloudflared'):
        for pid in process_name_pids(image_name):
            if pid != current_pid:
                terminate_pid(pid)


def health_url():
    return f'http://{local_access_host()}:{app_port()}/health'


def app_url():
    return f'http://{local_access_host()}:{app_port()}/'


def run_local_server():
    host = os.getenv('APP_HOST', '127.0.0.1')
    port = int(os.getenv('APP_PORT', '5000'))
    reset_server_log()
    append_server_log(f'Starting local server on {host}:{port}')

    try:
        from app import app

        try:
            from waitress import serve
        except ImportError:
            serve = None

        if serve is not None:
            append_server_log('Using waitress')
            serve(app, host=host, port=port, threads=8)
            return

        append_server_log('Using Flask built-in server')
        app.run(host=host, port=port, debug=False, use_reloader=False)
    except Exception:
        append_server_log(traceback.format_exc())
        raise


class LauncherUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title('Project HR Control')
        self.root.geometry('620x420')
        self.root.minsize(560, 360)
        self.root.protocol('WM_DELETE_WINDOW', self.on_close)

        env_map = read_env_map()
        self.server_process = None
        self.tunnel_process = None
        self.active_mode = None
        self.public_url = None
        self.pending_public_url = None
        self.online_started_at = None
        self.last_tunnel_error = None
        self.start_in_progress = False
        self.ui_actions = queue.Queue()
        self.status_var = tk.StringVar(value='Сервер не запущен')
        self.health_var = tk.StringVar(value=DEFAULT_HEALTH_TEXT)
        self.public_url_var = tk.StringVar(value=DEFAULT_PUBLIC_URL_TEXT)
        self.model_enabled_var = tk.BooleanVar(value=env_map.get('OLLAMA_ENABLED', '1').lower() in {'1', 'true', 'yes', 'on'})
        self.model_name_var = tk.StringVar(value=env_map.get('OLLAMA_MODEL', 'mistral'))
        self.run_mode_var = tk.StringVar(value='local')

        self.build_ui()
        self.root.after(100, self.process_ui_actions)
        self.refresh_health()

    def dispatch_ui(self, callback):
        self.ui_actions.put(callback)

    def process_ui_actions(self):
        while True:
            try:
                callback = self.ui_actions.get_nowait()
            except queue.Empty:
                break
            callback()

        self.root.after(100, self.process_ui_actions)

    def build_ui(self):
        self.root.configure(bg='#f4efe7')

        frame = ttk.Frame(self.root, padding=18)
        frame.pack(fill='both', expand=True)
        frame.columnconfigure(0, weight=1)

        title = ttk.Label(frame, text='Project HR Launcher', font=('Segoe UI', 16, 'bold'))
        title.grid(row=0, column=0, sticky='w')

        subtitle = ttk.Label(frame, text='Локальный, Docker и онлайн-запуск с управлением AI-моделью')
        subtitle.grid(row=1, column=0, sticky='w', pady=(4, 16))

        mode_box = ttk.LabelFrame(frame, text='Режим запуска', padding=12)
        mode_box.grid(row=2, column=0, sticky='ew')
        ttk.Radiobutton(mode_box, text='Локально', value='local', variable=self.run_mode_var).grid(row=0, column=0, sticky='w')
        ttk.Radiobutton(mode_box, text='Docker', value='docker', variable=self.run_mode_var).grid(row=0, column=1, sticky='w', padx=(16, 0))
        ttk.Radiobutton(mode_box, text='Онлайн', value='online', variable=self.run_mode_var).grid(row=0, column=2, sticky='w', padx=(16, 0))

        model_box = ttk.LabelFrame(frame, text='AI-модель', padding=12)
        model_box.grid(row=3, column=0, sticky='ew', pady=(14, 0))
        ttk.Checkbutton(model_box, text='Использовать Ollama', variable=self.model_enabled_var, command=self.save_settings).grid(row=0, column=0, sticky='w')
        ttk.Label(model_box, text='Модель').grid(row=1, column=0, sticky='w', pady=(10, 0))

        model_combo = ttk.Combobox(model_box, textvariable=self.model_name_var, values=['mistral', 'tinyllama'], state='normal')
        model_combo.grid(row=2, column=0, sticky='ew', pady=(6, 0))
        model_combo.bind('<<ComboboxSelected>>', lambda event: self.save_settings())
        model_combo.bind('<FocusOut>', lambda event: self.save_settings())
        model_box.columnconfigure(0, weight=1)

        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, sticky='ew', pady=(18, 0))
        buttons.columnconfigure((0, 1, 2, 3, 4), weight=1)

        ttk.Button(buttons, text='Запустить сервер', command=self.start_server).grid(row=0, column=0, sticky='ew', padx=(0, 6))
        ttk.Button(buttons, text='Остановить сервер', command=self.stop_server).grid(row=0, column=1, sticky='ew', padx=6)
        ttk.Button(buttons, text='Открыть приложение', command=self.open_app).grid(row=0, column=2, sticky='ew', padx=6)
        ttk.Button(buttons, text='Копировать ссылку', command=self.copy_online_link).grid(row=0, column=3, sticky='ew', padx=6)
        ttk.Button(buttons, text='Применить', command=self.save_settings).grid(row=0, column=4, sticky='ew', padx=(6, 0))

        status_box = ttk.LabelFrame(frame, text='Статус', padding=12)
        status_box.grid(row=5, column=0, sticky='nsew', pady=(18, 0))
        status_box.columnconfigure(0, weight=1)
        frame.rowconfigure(5, weight=1)

        ttk.Label(status_box, textvariable=self.status_var, font=('Segoe UI', 11, 'bold')).grid(row=0, column=0, sticky='w')
        ttk.Label(status_box, textvariable=self.health_var, justify='left').grid(row=1, column=0, sticky='w', pady=(10, 0))
        ttk.Label(status_box, textvariable=self.public_url_var, justify='left', foreground='#1d4ed8', wraplength=520).grid(row=2, column=0, sticky='w', pady=(10, 0))

    def save_settings(self):
        updates = {
            'OLLAMA_ENABLED': '1' if self.model_enabled_var.get() else '0',
            'OLLAMA_MODEL': self.model_name_var.get().strip() or 'mistral',
        }
        update_env_values(updates)
        self.status_var.set('Настройки сохранены')

    def build_launch_env(self):
        env = os.environ.copy()
        env_map = read_env_map()
        env['OPEN_BROWSER'] = '0'
        env['APP_PORT'] = app_port()
        env['OLLAMA_ENABLED'] = '1' if self.model_enabled_var.get() else '0'
        env['OLLAMA_MODEL'] = self.model_name_var.get().strip() or 'mistral'
        models_path = runtime_relative_path(env_map.get('OLLAMA_MODELS')) or default_ollama_models_path()
        if models_path:
            env['OLLAMA_MODELS'] = models_path
        return env

    def start_server(self):
        if self.start_in_progress:
            self.status_var.set('Запуск уже выполняется...')
            return

        self.save_settings()
        selected_port, port_message = choose_runtime_port()
        set_app_port_override(selected_port)
        self.start_in_progress = True
        launch_env = self.build_launch_env()
        if port_message:
            self.health_var.set(port_message)

        if self.run_mode_var.get() == 'online':
            self.status_var.set(ONLINE_STARTING_TEXT)
            threading.Thread(target=self.start_online_mode, args=(launch_env,), daemon=True).start()
            return

        if self.run_mode_var.get() == 'docker':
            self.status_var.set(DOCKER_STARTING_TEXT)
            threading.Thread(target=self.start_docker_mode, daemon=True).start()
            return

        self.status_var.set(LOCAL_STARTING_TEXT)
        threading.Thread(target=self.start_local_mode, args=(launch_env,), daemon=True).start()

    def finish_start(self):
        self.start_in_progress = False

    def start_local_mode(self, launch_env):
        reset_server_log()

        if self.is_server_responding():
            self.dispatch_ui(lambda: self.status_var.set('Сервер уже запущен'))
            self.dispatch_ui(self.finish_start)
            return

        command = server_command()

        creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        process = subprocess.Popen(command, cwd=server_cwd(), env=launch_env, creationflags=creationflags)

        def apply_started_process():
            self.server_process = process
            self.active_mode = 'local'
            self.status_var.set(LOCAL_STARTING_TEXT)
            self.finish_start()

        self.dispatch_ui(apply_started_process)

    def start_online_mode(self, launch_env):
        self.clear_public_url_state()
        cleanup_cloudflared_processes()
        self.online_started_at = time.time()
        self.last_tunnel_error = None

        if self.is_server_responding():
            def start_existing_server_tunnel():
                self.active_mode = 'online'
                self.status_var.set(ONLINE_STARTING_TEXT)
                self.start_tunnel_process()
                self.finish_start()

            self.dispatch_ui(start_existing_server_tunnel)
            return

        command = server_command()

        creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        process = subprocess.Popen(command, cwd=server_cwd(), env=launch_env, creationflags=creationflags)

        def apply_online_started_process():
            self.server_process = process
            self.active_mode = 'online'
            self.status_var.set(ONLINE_STARTING_TEXT)
            self.root.after(800, self.start_tunnel_when_ready)
            self.finish_start()

        self.dispatch_ui(apply_online_started_process)

    def start_tunnel_when_ready(self, attempt=0):
        if self.active_mode != 'online':
            return

        if self.is_server_responding():
            self.start_tunnel_process()
            return

        if self.server_process and self.server_process.poll() is None and attempt < 30:
            self.root.after(500, lambda: self.start_tunnel_when_ready(attempt + 1))
            return

        self.status_var.set('Не удалось запустить локальный сервер для онлайн-режима')

    def start_tunnel_process(self):
        command = tunnel_command()
        if command is None:
            messagebox.showerror(
                'Project HR',
                'Для онлайн-режима нужен cloudflared. Установите его командой:\nwinget install Cloudflare.cloudflared'
            )
            self.public_url_var.set('Онлайн-ссылка недоступна: cloudflared не установлен')
            return

        if self.tunnel_process and self.tunnel_process.poll() is None:
            return

        self.last_tunnel_error = None
        creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        self.tunnel_process = subprocess.Popen(
            command,
            cwd=server_cwd(),
            creationflags=creationflags,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='ignore',
        )
        threading.Thread(target=self.monitor_tunnel_output, daemon=True).start()

    def monitor_tunnel_output(self):
        if not self.tunnel_process or self.tunnel_process.stdout is None:
            return

        for raw_line in self.tunnel_process.stdout:
            match = TRYCLOUDFLARE_URL_PATTERN.search(raw_line)
            if match:
                self.dispatch_ui(lambda url=match.group(0): self.prepare_public_url_validation(url))
                return

            if 'ERR' in raw_line or 'error=' in raw_line.lower() or 'failed' in raw_line.lower():
                message = raw_line.strip()
                self.dispatch_ui(lambda value=message: self.set_tunnel_error(value))

        if self.active_mode == 'online' and not self.public_url:
            self.dispatch_ui(self.handle_tunnel_unavailable)

    def prepare_public_url_validation(self, url):
        self.accept_public_url_without_validation(url, 'Онлайн-ссылка получена. Если страница не открывается сразу, подождите 10-20 секунд и попробуйте снова.')
        threading.Thread(target=self.validate_public_url, args=(url,), daemon=True).start()

    def validate_public_url(self, url):
        request = Request(url, headers={'User-Agent': 'ProjectHR/1.0'})
        for _ in range(6):
            if self.active_mode != 'online' or (self.pending_public_url != url and self.public_url != url):
                return
            try:
                with urlopen(request, timeout=5) as response:
                    if 200 <= response.status < 400:
                        self.dispatch_ui(lambda value=url: self.set_public_url(value))
                        return
            except HTTPError as error:
                if error.code == 530:
                    time.sleep(2)
                    continue
                self.dispatch_ui(lambda value=url, message=f'Онлайн-ссылка получена, но авто-проверка вернула HTTP {error.code}. Откройте ссылку вручную.': self.accept_public_url_without_validation(value, message))
                return
            except Exception:
                time.sleep(2)

        self.dispatch_ui(lambda value=url: self.accept_public_url_without_validation(value, 'Онлайн-ссылка получена, но авто-проверка не подтвердила доступность. Подождите 10-20 секунд и откройте ссылку вручную.'))

    def set_public_url(self, url):
        self.pending_public_url = None
        self.public_url = url
        self.last_tunnel_error = None
        self.sync_public_url_display()
        if self.active_mode == 'online' and self.is_server_responding():
            self.status_var.set('Онлайн-режим запущен')

    def accept_public_url_without_validation(self, url, message):
        if self.active_mode != 'online':
            return
        self.pending_public_url = None
        self.public_url = url
        self.last_tunnel_error = message
        self.public_url_var.set(f'Онлайн-ссылка: {url}')
        self.status_var.set(ONLINE_READY_WITH_WARNING_TEXT)
        self.health_var.set(message[:240])

    def sync_public_url_display(self):
        if self.public_url:
            self.public_url_var.set(f'Онлайн-ссылка: {self.public_url}')
        elif self.pending_public_url:
            self.public_url_var.set(f'Проверка онлайн-ссылки: {self.pending_public_url}')
        elif not self.last_tunnel_error:
            self.public_url_var.set(DEFAULT_PUBLIC_URL_TEXT)

    def set_tunnel_error(self, message):
        self.last_tunnel_error = message
        if self.active_mode == 'online' and not self.public_url:
            self.public_url_var.set(f'Ошибка tunnel: {message[:240]}')

    def fail_online_mode(self, message, stop_tunnel=True):
        self.last_tunnel_error = message
        self.pending_public_url = None
        self.public_url = None
        self.online_started_at = None
        if stop_tunnel and self.tunnel_process and self.tunnel_process.poll() is None:
            self.tunnel_process.terminate()
        self.tunnel_process = None
        self.status_var.set(ONLINE_FAILED_TEXT)
        self.public_url_var.set(message[:240])

    def handle_tunnel_unavailable(self):
        message = self.last_tunnel_error or 'Онлайн-ссылка не получена. Проверьте cloudflared и интернет-соединение.'
        self.fail_online_mode(message)

    def online_mode_timed_out(self):
        if self.active_mode != 'online' or self.public_url or not self.online_started_at:
            return False
        return (time.time() - self.online_started_at) >= ONLINE_START_TIMEOUT_SECONDS

    def reset_public_url(self):
        self.clear_public_url_state()
        self.sync_public_url_display()

    def clear_public_url_state(self):
        self.public_url = None
        self.pending_public_url = None
        self.online_started_at = None
        self.last_tunnel_error = None

    def copy_online_link(self):
        link_to_copy = self.public_url or self.pending_public_url
        if not link_to_copy:
            self.status_var.set('Онлайн-ссылка пока недоступна')
            return

        self.root.clipboard_clear()
        self.root.clipboard_append(link_to_copy)
        self.root.update()
        self.status_var.set('Онлайн-ссылка скопирована')

    def stop_tunnel_process(self):
        if self.tunnel_process and self.tunnel_process.poll() is None:
            self.tunnel_process.terminate()
        self.tunnel_process = None
        cleanup_cloudflared_processes()
        self.reset_public_url()

    def start_docker_mode(self):
        if shutil.which('docker') is None:
            self.dispatch_ui(lambda: messagebox.showerror('Project HR', 'Docker не найден. Установите Docker Desktop или используйте локальный режим.'))
            self.dispatch_ui(self.finish_start)
            return

        env = os.environ.copy()
        creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        process = subprocess.Popen(
            docker_command('up', '--build'),
            cwd=server_cwd(),
            env=env,
            creationflags=creationflags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        def apply_docker_started_process():
            self.server_process = process
            self.active_mode = 'docker'
            self.status_var.set(DOCKER_STARTING_TEXT)
            self.finish_start()

        self.dispatch_ui(apply_docker_started_process)

    def stop_server(self):
        if self.active_mode == 'docker':
            self.stop_docker_mode()
            return

        self.stop_tunnel_process()

        if self.server_process and self.server_process.poll() is None:
            self.server_process.terminate()
            self.server_process = None
            self.active_mode = None
            set_app_port_override(None)
            self.status_var.set('Сервер остановлен')
            self.health_var.set(DEFAULT_HEALTH_TEXT)
            return

        self.active_mode = None
        set_app_port_override(None)
        self.status_var.set('Нет управляемого процесса для остановки')

    def stop_docker_mode(self):
        if shutil.which('docker') is None:
            self.status_var.set('Docker не найден')
            return

        try:
            subprocess.run(
                docker_command('down'),
                cwd=server_cwd(),
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        finally:
            self.stop_tunnel_process()
            if self.server_process and self.server_process.poll() is None:
                self.server_process.terminate()
            self.server_process = None
            self.active_mode = None
            set_app_port_override(None)
            self.status_var.set('Docker-режим остановлен')
            self.health_var.set(DEFAULT_HEALTH_TEXT)

    def open_app(self):
        online_link = self.public_url or self.pending_public_url
        if self.active_mode == 'online' and online_link:
            webbrowser.open(online_link)
            return
        webbrowser.open(app_url())

    def is_server_responding(self):
        try:
            with urlopen(health_url(), timeout=1.5) as response:
                return response.status == 200
        except Exception:
            return False

    def sync_process_state(self):
        if self.server_process and self.server_process.poll() is not None:
            exit_code = self.server_process.returncode
            self.server_process = None
            if self.active_mode != 'online' or not self.tunnel_process:
                self.active_mode = None
            if exit_code and not self.is_server_responding():
                log_tail = read_server_log_tail()
                if log_tail:
                    self.status_var.set(f'Ошибка запуска сервера (код {exit_code})')
                    self.health_var.set(log_tail)
                else:
                    self.status_var.set(f'Сервер завершился с кодом {exit_code}')

        if self.tunnel_process and self.tunnel_process.poll() is not None:
            self.tunnel_process = None
            if self.active_mode == 'online' and self.public_url:
                self.public_url_var.set('Онлайн-ссылка завершена. Запустите режим заново.')
                self.public_url = None
            elif self.active_mode == 'online' and self.pending_public_url:
                self.fail_online_mode('Tunnel завершился до получения рабочей ссылки.', stop_tunnel=False)

    def set_running_status(self):
        if self.active_mode == 'docker':
            self.status_var.set('Docker-режим запущен')
            return

        if self.active_mode == 'online':
            self.sync_public_url_display()
            if self.public_url:
                if self.last_tunnel_error:
                    self.status_var.set(ONLINE_READY_WITH_WARNING_TEXT)
                else:
                    self.status_var.set('Онлайн-режим запущен')
            elif self.pending_public_url:
                self.status_var.set(ONLINE_VALIDATING_TEXT)
            elif self.last_tunnel_error:
                self.status_var.set(ONLINE_FAILED_TEXT)
            else:
                self.status_var.set(ONLINE_STARTING_TEXT)
            return

        self.status_var.set('Сервер запущен')

    def set_starting_status(self):
        if self.active_mode == 'docker':
            self.status_var.set(DOCKER_STARTING_TEXT)
            return

        if self.active_mode == 'online':
            self.status_var.set(ONLINE_STARTING_TEXT)
            return

        self.status_var.set(LOCAL_STARTING_TEXT)

    def handle_health_success(self, payload):
        if self.online_mode_timed_out():
            self.handle_tunnel_unavailable()
            self.health_var.set(payload)
            return
        self.set_running_status()
        self.health_var.set(payload)

    def handle_health_unavailable(self):
        if self.online_mode_timed_out():
            self.handle_tunnel_unavailable()
            self.health_var.set('Нет ответа от /health')
            return

        if self.server_process and self.server_process.poll() is None:
            self.set_starting_status()
        else:
            self.status_var.set('Сервер не запущен')
            if self.active_mode != 'online':
                self.reset_public_url()
        self.health_var.set('Нет ответа от /health')

    def refresh_health(self):
        self.sync_process_state()

        try:
            with urlopen(health_url(), timeout=1.5) as response:
                self.handle_health_success(response.read().decode('utf-8'))
        except URLError:
            self.handle_health_unavailable()
        except Exception as error:
            self.health_var.set(f'Ошибка проверки: {error}')

        self.root.after(2000, self.refresh_health)

    def on_close(self):
        if self.active_mode == 'docker':
            self.stop_docker_mode()
        else:
            self.stop_tunnel_process()
            if self.server_process and self.server_process.poll() is None:
                self.server_process.terminate()
            set_app_port_override(None)
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    if '--serve-local' in sys.argv:
        run_local_server()
        return

    LauncherUI().run()


if __name__ == '__main__':
    main()
