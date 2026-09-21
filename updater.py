# updater.py
"""
Модуль автоматической синхронизации кода с GitHub-репозиторием.
Использует только стандартную библиотеку Python — без pip install.

Логика:
    • Раз в CHECK_INTERVAL секунд делается git fetch в FETCH_HEAD.
    • Сравнивается содержимое рабочего дерева с FETCH_HEAD.
    • Если есть расхождения (кроме IGNORE_FILES) — рабочее дерево
      принудительно приводится к состоянию FETCH_HEAD.
    • HEAD при этом не двигается (если HARD_RESET=false), но файлы
      на диске становятся ровно такими, как в репозитории.

Настройка через переменные окружения:
    GITHUB_REPO      — "owner/repo"          (обязательно)
    GITHUB_BRANCH    — ветка, по умолчанию "main"
    GITHUB_TOKEN     — personal access token (обязательно для приватного репо)
    CHECK_INTERVAL   — интервал в секундах, по умолчанию 60
    AUTO_UPDATE      — "true"/"false", по умолчанию true
    HARD_RESET       — "true"/"false", по умолчанию false
                       (если true — дополнительно git reset --hard FETCH_HEAD)
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
import logging
import subprocess
import threading
from datetime import datetime, timezone


# ---------- Конфигурация из окружения ----------
GITHUB_REPO    = os.environ.get("GITHUB_REPO", "Mi-Gear/Pulse")
GITHUB_BRANCH  = os.environ.get("GITHUB_BRANCH", "main")
GITHUB_TOKEN   = os.environ.get(
    "GITHUB_TOKEN",
    "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",
)
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "600"))
AUTO_UPDATE    = os.environ.get("AUTO_UPDATE", "true").lower() in ("1", "true", "yes", "on")
HARD_RESET     = os.environ.get("HARD_RESET", "false").lower() in ("1", "true", "yes", "on")

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
}

# Паттерны для git clean -e (что не удалять при очистке untracked)
CLEAN_KEEP_PATTERNS = [".env", "*.log", "__pycache__/", ".venv/", "venv/"]


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
    Периодически синхронизирует локальный код с указанной веткой GitHub-репозитория.
    Сравнение идёт по содержимому файлов (git diff FETCH_HEAD), а не по SHA коммита.
    При обнаружении расхождений рабочее дерево приводится к состоянию FETCH_HEAD.
    """

    def __init__(self, repo, branch="main", token=None,
                 interval=60, auto_update=True, hard_reset=False,
                 repo_dir=None):
        if not repo:
            raise ValueError("GITHUB_REPO не задан")
        if not token:
            raise ValueError("GITHUB_TOKEN обязателен для приватного репозитория")

        self.repo = repo
        self.branch = branch
        self.token = token
        self.interval = interval
        self.auto_update = auto_update
        self.hard_reset = hard_reset
        self.repo_dir = repo_dir or REPO_DIR

        self._stop_event = threading.Event()
        self._thread = None
        self._lock = threading.Lock()

        # Состояние для ручной диагностики
        self.last_check = None
        self.last_changed_files = []
        self.update_available = False

    # ---------- URL с токеном (только для git fetch/pull) ----------
    def _auth_url(self):
        return f"https://x-access-token:{self.token}@github.com/{self.repo}.git"

    # ---------- Маскировка токена в выводе git ----------
    def _redact(self, text):
        if self.token and self.token in text:
            return text.replace(self.token, "***")
        return text

    # ---------- git fetch ----------
    def _fetch(self):
        try:
            r = subprocess.run(
                ["git", "fetch", "--prune", self._auth_url(), self.branch],
                cwd=self.repo_dir,
                capture_output=True, text=True, timeout=120,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
            if r.returncode == 0:
                return True
            update_logger.error(
                "git fetch ошибка (rc=%s):\nSTDOUT: %s\nSTDERR: %s",
                r.returncode, r.stdout.strip(), self._redact(r.stderr.strip()),
            )
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            update_logger.error("git fetch исключение: %s", e)
        return False

    # ---------- Список расходящихся файлов ----------
    def _get_changed_files(self):
        """
        Возвращает список файлов, которые отличаются между локальным
        рабочим деревом и FETCH_HEAD. Пустой список = обновлений нет.
        None = ошибка git.
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
                    r.returncode, self._redact(r.stderr.strip()),
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

    # ---------- Принудительная синхронизация с FETCH_HEAD ----------
    def _sync_from_fetch_head(self):
        """
        Приводит рабочее дерево и индекс к состоянию FETCH_HEAD.
        HEAD не двигается (если hard_reset=False).
        Возвращает True при успехе.
        """
        try:
            # 1. Восстанавливаем отслеживаемые файлы из FETCH_HEAD
            r = subprocess.run(
                ["git", "restore",
                 "--source=FETCH_HEAD",
                 "--worktree",
                 "--staged",
                 "--", "."],
                cwd=self.repo_dir,
                capture_output=True, text=True, timeout=120,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
            if r.returncode != 0:
                update_logger.error(
                    "git restore ошибка (rc=%s):\nSTDOUT: %s\nSTDERR: %s",
                    r.returncode, r.stdout.strip(), self._redact(r.stderr.strip()),
                )
                return False
            if r.stdout.strip():
                update_logger.info("git restore:\n%s", r.stdout.strip())

            # 2. Удаляем untracked-файлы, которые мешают
            clean_cmd = ["git", "clean", "-fd"]
            for pat in CLEAN_KEEP_PATTERNS:
                clean_cmd += ["-e", pat]
            r2 = subprocess.run(
                clean_cmd,
                cwd=self.repo_dir,
                capture_output=True, text=True, timeout=60,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
            if r2.returncode == 0 and r2.stdout.strip():
                update_logger.info("git clean -fd:\n%s", r2.stdout.strip())

            # 3. Опционально — жёсткий сброс HEAD
            if self.hard_reset:
                r3 = subprocess.run(
                    ["git", "reset", "--hard", "FETCH_HEAD"],
                    cwd=self.repo_dir,
                    capture_output=True, text=True, timeout=60,
                    env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
                )
                if r3.returncode != 0:
                    update_logger.error(
                        "git reset --hard ошибка (rc=%s): %s",
                        r3.returncode, self._redact(r3.stderr.strip()),
                    )
                    return False
                update_logger.info("git reset --hard FETCH_HEAD: %s", r3.stdout.strip())

            return True
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            update_logger.error("git sync исключение: %s", e)
            return False

    # ---------- Одна итерация проверки ----------
    def check_once(self):
        """
        Одна итерация. Возвращает True, если было расхождение
        и код был синхронизирован с репозиторием.
        """
        with self._lock:
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
                "Обнаружено расхождение. Файлов: %d — %s",
                len(changed), ", ".join(changed),
            )

            if not self.auto_update:
                update_logger.info(
                    "AUTO_UPDATE=false — синхронизация не выполняется. "
                    "Запустите вручную: git fetch && git restore --source=FETCH_HEAD -- ."
                )
                return True

            # 3. Синхронизируем рабочее дерево с FETCH_HEAD
            if self._sync_from_fetch_head():
                # 4. Финальная проверка
                still = self._get_changed_files()
                if still:
                    update_logger.error(
                        "После синхронизации всё ещё есть расхождения: %s. "
                        "Проверьте репозиторий вручную.",
                        ", ".join(still),
                    )
                    return False
                self.update_available = False
                update_logger.info(
                    "Код синхронизирован с GitHub (%s@%s). "
                    "Перезапустите сервис, если это требуется.",
                    self.repo, self.branch,
                )
                return True
            else:
                update_logger.error(
                    "Не удалось синхронизировать рабочее дерево с FETCH_HEAD."
                )
                return False

    # ---------- Цикл ----------
    def _run(self):
        update_logger.info(
            "Сервис обновлений запущен: repo=%s branch=%s interval=%ss "
            "auto=%s hard_reset=%s repo_dir=%s",
            self.repo, self.branch, self.interval,
            self.auto_update, self.hard_reset, self.repo_dir,
        )
        while not self._stop_event.is_set():
            try:
                self.check_once()
            except Exception:
                update_logger.exception("Ошибка в цикле проверки")
            self._stop_event.wait(self.interval)
        update_logger.info("Сервис обновлений остановлен.")

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
        hard_reset=HARD_RESET,
        repo_dir=REPO_DIR,
    )
else:
    update_logger.warning(
        "Сервис обновлений не запущен: задайте GITHUB_REPO и GITHUB_TOKEN."
    )