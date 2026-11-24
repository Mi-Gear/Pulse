"""Контроллер Pulse: проверяет GitHub во время работы и обновляет между запусками.

Основной процесс-контроллер не импортирует Flask. Сам сервер работает в
дочернем процессе. При обнаружении нового коммита контроллер сначала
останавливает дочерний процесс, затем обновляет рабочее дерево и запускает
новый процесс с актуальным кодом.
"""

import base64
import os
import signal
import shutil
import subprocess
import sys
import threading
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent


def _load_env_file():
    """Загружает локальный .env без сторонних зависимостей.

    Переменные окружения, заданные системой, имеют приоритет над .env.
    """
    env_path = REPO_DIR / ".env"
    if not env_path.is_file():
        return

    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        # Пустая системная переменная не должна блокировать значение из .env.
        if os.environ.get(key, "").strip():
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        os.environ[key] = value


_load_env_file()
APP_FILE = REPO_DIR / "app.py"
GITHUB_REPO = os.environ.get("GITHUB_REPO", "Mi-Gear/Pulse")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
CHECK_INTERVAL = max(10, int(os.environ.get("CHECK_INTERVAL", "60")))
AUTO_UPDATE = os.environ.get("AUTO_UPDATE", "true").lower() in {
    "1", "true", "yes", "on"
}
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip() or None


def _find_git_executable():
    """Находит Git даже если он установлен, но не добавлен в PATH."""
    git = shutil.which("git")
    if git:
        return git

    candidates = [
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git" / "cmd" / "git.exe",
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git" / "bin" / "git.exe",
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Git" / "cmd" / "git.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Git" / "cmd" / "git.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


GIT_EXECUTABLE = _find_git_executable()
LOCAL_FILES = ("config.py",)
STOP_EVENT = threading.Event()


def _git(*args, timeout=120):
    env = os.environ.copy()
    # Не позволяем Git Credential Manager открывать браузер или диалог входа.
    # Авторизация автообновления выполняется только через GITHUB_TOKEN из .env.
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    env["GIT_ASKPASS"] = ""
    env["SSH_ASKPASS"] = ""
    if not GIT_EXECUTABLE:
        raise FileNotFoundError(
            "Git не найден. Установите Git for Windows или добавьте git.exe в PATH."
        )

    # Репозиторий приватный: авторизуем fetch токеном из .env.
    # Отключаем credential helper, чтобы Git не открывал браузерный вход.
    command = [
        GIT_EXECUTABLE,
        "-c", "credential.helper=",
        "-c", "credential.interactive=never",
    ]
    if not GITHUB_TOKEN:
        raise RuntimeError(
            "Не задан GITHUB_TOKEN. Проверьте .env в корне Pulse."
        )

    auth = base64.b64encode(
        f"Mi-Gear:{GITHUB_TOKEN}".encode("utf-8")
    ).decode("ascii")
    command += [
        "-c",
        f"http.https://github.com/.extraheader=AUTHORIZATION: basic {auth}",
    ]
    return subprocess.run(
        command + list(args),
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def _fetch_latest():
    remote_url = f"https://github.com/{GITHUB_REPO}.git"
    result = _git(
        "fetch", "--no-tags", remote_url, GITHUB_BRANCH, timeout=180
    )
    if result.returncode != 0:
        print(
            "[UPDATE] Не удалось проверить GitHub; Pulse продолжает работу. "
            + result.stderr.strip(),
            flush=True,
        )
        return None

    local = _git("rev-parse", "HEAD", timeout=10)
    remote = _git("rev-parse", "FETCH_HEAD", timeout=10)
    if local.returncode != 0 or remote.returncode != 0:
        print("[UPDATE] Не удалось определить версии Git.", flush=True)
        return None

    local_sha = local.stdout.strip()
    remote_sha = remote.stdout.strip()
    if local_sha == remote_sha:
        print("[UPDATE] Обновлений не найдено.", flush=True)
        return None
    return remote_sha


def _install_update():
    """Устанавливает уже полученный FETCH_HEAD. Вызывать только после stop."""
    backups = {}
    for filename in LOCAL_FILES:
        path = REPO_DIR / filename
        if path.is_file():
            backups[filename] = (path.read_bytes(), path.stat().st_mode)

    result = _git("reset", "--hard", "FETCH_HEAD", timeout=180)
    if result.returncode != 0:
        print(
            "[UPDATE] Не удалось установить обновление. "
            "Pulse не будет запущен на неполной версии. "
            + result.stderr.strip(),
            flush=True,
        )
        return False

    for filename, (content, mode) in backups.items():
        path = REPO_DIR / filename
        path.write_bytes(content)
        try:
            path.chmod(mode)
        except OSError:
            pass

    print("[UPDATE] Файлы обновлены.", flush=True)
    return True


def _start_worker():
    env = os.environ.copy()
    env["PULSE_WORKER"] = "1"

    # При запуске через run.vbs / скрытый PowerShell стандартные
    # консольные дескрипторы могут отсутствовать или быть недействительными.
    # Не наследуем их: выдаём дочернему процессу отдельный лог-файл.
    log_dir = REPO_DIR / "logs"
    log_dir.mkdir(exist_ok=True)
    worker_log = log_dir / "worker.log"

    with worker_log.open("a", encoding="utf-8", buffering=1) as output:
        return subprocess.Popen(
            [sys.executable, str(APP_FILE)],
            cwd=str(REPO_DIR),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )


def _stop_worker(process):
    if process is None or process.poll() is not None:
        return
    print("[UPDATE] Останавливаю Pulse перед обновлением...", flush=True)
    process.terminate()
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        print("[UPDATE] Pulse не завершился штатно; принудительная остановка.", flush=True)
        process.kill()
        process.wait()


def _handle_signal(_signum, _frame):
    STOP_EVENT.set()


def run_supervisor():
    """Запускает Pulse и проверяет обновления, пока сервер работает."""
    if not APP_FILE.is_file():
        raise RuntimeError(f"Не найден файл приложения: {APP_FILE}")

    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)

    print(
        f"[UPDATE] Контроллер запущен. Проверка {GITHUB_REPO}@{GITHUB_BRANCH} "
        f"каждые {CHECK_INTERVAL} сек.",
        flush=True,
    )

    worker = _start_worker()

    while not STOP_EVENT.is_set():
        if STOP_EVENT.wait(CHECK_INTERVAL):
            break

        if not AUTO_UPDATE:
            continue

        try:
            new_sha = _fetch_latest()
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            print(f"[UPDATE] Ошибка проверки GitHub: {exc}", flush=True)
            new_sha = None

        if new_sha:
            print(
                f"[UPDATE] Доступно обновление {new_sha[:8]}.",
                flush=True,
            )
            _stop_worker(worker)
            worker = None

            if not _install_update():
                print(
                    "[UPDATE] Обновление не установлено. "
                    "Сервер оставлен остановленным для безопасности.",
                    flush=True,
                )
                return

            print(
                "[UPDATE] Запускаю новый контроллер с обновлённой версией...",
                flush=True,
            )
            env = os.environ.copy()
            env.pop("PULSE_WORKER", None)
            try:
                # os.execve ненадёжен/неподдерживается для такого сценария
                # на Windows. Запускаем новый процесс и завершаем старый.
                subprocess.Popen(
                    [sys.executable, str(APP_FILE)],
                    cwd=str(REPO_DIR),
                    env=env,
                    close_fds=True,
                )
                print("[UPDATE] Новый контроллер запущен.", flush=True)
                return
            except OSError as exc:
                print(
                    f"[UPDATE] Не удалось перезапустить контроллер: {exc}. "
                    "Пробую запустить сервер в текущем контроллере.",
                    flush=True,
                )
                worker = _start_worker()

        # Если дочерний сервер аварийно завершился, поднимаем его снова.
        if worker.poll() is not None:
            print(
                f"[PULSE] Процесс завершился с кодом {worker.returncode}; "
                "перезапускаю через 3 секунды.",
                flush=True,
            )
            if STOP_EVENT.wait(3):
                break
            worker = _start_worker()

    _stop_worker(worker)
    print("[UPDATE] Контроллер Pulse остановлен.", flush=True)
