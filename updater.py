# updater.py
"""
Модуль автоматической проверки обновлений из приватного GitHub-репозитория.
Использует только стандартную библиотеку Python — без pip install.

Настройка через переменные окружения:
    GITHUB_REPO      — "owner/repo"          (обязательно)
    GITHUB_BRANCH    — ветка, по умолчанию "main"
    GITHUB_TOKEN     — personal access token (обязательно для приватного репо)
    CHECK_INTERVAL   — интервал в секундах, по умолчанию 3600
    AUTO_UPDATE      — "true"/"false", по умолчанию false (делать git pull)
    REPO_DIR         — путь к локальному git-репозиторию
                       (по умолчанию — директория этого файла)

Использование:
    from updater import update_checker
    ...
    if update_checker:
        update_checker.start()
    ...
    update_checker.stop()
"""

import os
import json
import logging
import subprocess
import threading
import urllib.request
import urllib.error
from datetime import datetime, timezone


# ---------- Конфигурация из окружения ----------
GITHUB_REPO    = os.environ.get("GITHUB_REPO", "Mi-Gear/Pulse")
GITHUB_BRANCH  = os.environ.get("GITHUB_BRANCH", "main")
GITHUB_TOKEN   = os.environ.get(
    "GITHUB_TOKEN",
    "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",
)
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "10"))
AUTO_UPDATE    = os.environ.get("AUTO_UPDATE", "true").lower() in ("1", "true", "yes", "on")

# Директория git-репозитория. По умолчанию — папка, где лежит этот файл.
REPO_DIR = os.environ.get(
    "REPO_DIR",
    os.path.dirname(os.path.abspath(__file__)),
)

# Файлы, различия в которых НЕ считаются обновлением кода
# (локальные конфиги, секреты и т.п.)
IGNORE_FILES = {
    ".env",
    "config.py",
    "local_settings.py",
    "updater.py",  # сам файл может отличаться — токен захардкожен
}


# ---------- Логгер ----------
update_logger = logging.getLogger("auto_update")
update_logger.setLevel(logging.INFO)
if not update_logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter(
        "[%(asctime)s] [UPDATE] %(levelname)s: %(message)s"
    ))
    update_logger.addHandler(_h)


class GitHubUpdateChecker:
    """
    Периодически (раз в CHECK_INTERVAL секунд) проверяет,
    появились ли новые ИЗМЕНЕНИЯ В ФАЙЛАХ в указанной ветке GitHub-репозитория.
    Сравнение идёт по содержимому (git diff), а не по SHA коммита.
    Опционально делает `git pull`.
    """

    def __init__(self, repo, branch="main", token=None,
                 interval=3600, auto_update=False, repo_dir=None):
        if not repo:
            raise ValueError("GITHUB_REPO не задан")
        if not token:
            raise ValueError("GITHUB_TOKEN обязателен для приватного репозитория")

        self.repo = repo
        self.branch = branch
        self.token = token
        self.interval = interval
        self.auto_update = auto_update
        self.repo_dir = repo_dir or REPO_DIR

        self._stop_event = threading.Event()
        self._thread = None

        # Состояние для ручной диагностики
        self.last_check = None
        self.last_changed_files = []
        self.update_available = False

    # ---------- Вспомогательное: URL с токеном ----------
    def _auth_url(self):
        return f"https://x-access-token:{self.token}@github.com/{self.repo}.git"

    # ---------- git fetch ----------
    def _fetch(self):
        try:
            r = subprocess.run(
                ["git", "fetch", self._auth_url(), self.branch],
                cwd=self.repo_dir,
                capture_output=True, text=True, timeout=120,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
            if r.returncode == 0:
                return True
            update_logger.error(
                "git fetch ошибка (rc=%s):\nSTDOUT: %s\nSTDERR: %s",
                r.returncode, r.stdout.strip(), r.stderr.strip(),
            )
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            update_logger.error("git fetch исключение: %s", e)
        return False

    # ---------- Проверка различий по файлам ----------
    def _get_changed_files(self):
        """
        Возвращает список файлов, которые отличаются между локальным
        рабочим деревом и origin/<branch>. Пустой список = обновлений нет.

        Использует FETCH_HEAD, потому что после `git fetch <url> <branch>`
        именно туда попадает свежий коммит (origin/<branch> может быть не обновлён).
        """
        try:
            r = subprocess.run(
                ["git", "diff", "--name-only", "FETCH_HEAD"],
                cwd=self.repo_dir,
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode != 0:
                update_logger.error(
                    "git diff ошибка (rc=%s): %s",
                    r.returncode, r.stderr.strip(),
                )
                return None

            changed = [
                f.strip() for f in r.stdout.splitlines()
                if f.strip() and f.strip() not in IGNORE_FILES
            ]
            return changed
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            update_logger.error("git diff исключение: %s", e)
            return None

    # ---------- git pull ----------
    def _pull(self):
        try:
            r = subprocess.run(
                ["git", "pull", self._auth_url(), self.branch],
                cwd=self.repo_dir,
                capture_output=True, text=True, timeout=120,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
            if r.returncode == 0:
                update_logger.info("git pull OK:\n%s", r.stdout.strip())
                return True
            update_logger.error(
                "git pull ошибка (rc=%s):\nSTDOUT: %s\nSTDERR: %s",
                r.returncode, r.stdout.strip(), r.stderr.strip(),
            )
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            update_logger.error("git pull исключение: %s", e)
        return False

    # ---------- Цикл ----------
    def _run(self):
        update_logger.info(
            "Сервис обновлений запущен: repo=%s branch=%s interval=%ss "
            "auto=%s repo_dir=%s",
            self.repo, self.branch, self.interval,
            self.auto_update, self.repo_dir,
        )
        while not self._stop_event.is_set():
            try:
                self.check_once()
            except Exception:
                update_logger.exception("Ошибка в цикле проверки")
            self._stop_event.wait(self.interval)
        update_logger.info("Сервис обновлений остановлен.")

    def check_once(self):
        """Одна итерация проверки. Возвращает True, если есть обновление."""
        self.last_check = datetime.now(timezone.utc)

        # 1. Свежий fetch (в FETCH_HEAD)
        if not self._fetch():
            update_logger.warning("Не удалось получить свежие данные с GitHub.")
            return False

        # 2. Что отличается по содержимому?
        changed = self._get_changed_files()
        if changed is None:
            return False

        self.last_changed_files = changed

        if not changed:
            self.update_available = False
            update_logger.info("Обновлений нет (файлы идентичны).")
            return False

        self.update_available = True
        update_logger.warning(
            "Доступно обновление. Изменены файлы (%d): %s",
            len(changed), ", ".join(changed),
        )

        if self.auto_update:
            if self._pull():
                update_logger.info("Обновление применено. Перезапустите сервис.")
            else:
                update_logger.error("Не удалось применить обновление.")
        else:
            update_logger.info("Выполните вручную: git pull origin %s", self.branch)

        return True

    # ---------- Управление потоком ----------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="GitHubUpdateChecker", daemon=True
        )
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)


# ---------- Готовый экземпляр ----------
update_checker = None
if GITHUB_REPO and GITHUB_TOKEN:
    update_checker = GitHubUpdateChecker(
        repo=GITHUB_REPO,
        branch=GITHUB_BRANCH,
        token=GITHUB_TOKEN,
        interval=CHECK_INTERVAL,
        auto_update=AUTO_UPDATE,
        repo_dir=REPO_DIR,
    )
else:
    update_logger.warning(
        "Сервис обновлений не запущен: задайте GITHUB_REPO и GITHUB_TOKEN."
    )