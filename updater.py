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
from datetime import datetime


# ---------- Конфигурация из окружения ----------
GITHUB_REPO    = "Mi-Gear/Pulse"
GITHUB_BRANCH  = os.environ.get("GITHUB_BRANCH", "main")
GITHUB_TOKEN   = "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"
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "10"))
AUTO_UPDATE    = True


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
    появились ли новые коммиты в указанной ветке GitHub-репозитория.
    Опционально делает `git pull`.
    """

    def __init__(self, repo, branch="main", token=None,
                 interval=3600, auto_update=False):
        if not repo:
            raise ValueError("GITHUB_REPO не задан")
        if not token:
            raise ValueError("GITHUB_TOKEN обязателен для приватного репозитория")

        self.repo = repo
        self.branch = branch
        self.token = token
        self.interval = interval
        self.auto_update = auto_update

        self._stop_event = threading.Event()
        self._thread = None

        # Состояние для ручной диагностики
        self.last_check = None
        self.last_remote_sha = None
        self.last_local_sha = None
        self.update_available = False

    # ---------- GitHub API через urllib ----------
    def _get_remote_sha(self):
        url = f"https://api.github.com/repos/{self.repo}/commits/{self.branch}"
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "servicedesk-updater",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("sha")
        except urllib.error.HTTPError as e:
            if e.code == 401:
                update_logger.error("GitHub: неверный токен (401).")
            elif e.code == 404:
                update_logger.error(
                    "GitHub: репо/ветка не найдены (404). "
                    "Проверьте GITHUB_REPO, GITHUB_BRANCH и права токена."
                )
            else:
                update_logger.warning("GitHub API HTTP %s", e.code)
        except (urllib.error.URLError, TimeoutError) as e:
            update_logger.error("Сеть недоступна: %s", e)
        return None

    # ---------- Локальный SHA ----------
    def _get_local_sha(self):
        try:
            r = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=10
            )
            if r.returncode == 0:
                return r.stdout.strip()
            update_logger.warning("git rev-parse: %s", r.stderr.strip())
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            update_logger.error("git недоступен: %s", e)
        return None

    # ---------- git pull ----------
    def _pull(self):
        try:
            r = subprocess.run(
                ["git", "pull", "origin", self.branch],
                capture_output=True, text=True, timeout=120,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
            if r.returncode == 0:
                update_logger.info("git pull OK:\n%s", r.stdout.strip())
                return True
            update_logger.error("git pull ошибка:\n%s", r.stderr.strip())
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            update_logger.error("git pull исключение: %s", e)
        return False

    # ---------- Цикл ----------
    def _run(self):
        update_logger.info(
            "Сервис обновлений запущен: repo=%s branch=%s interval=%ss auto=%s",
            self.repo, self.branch, self.interval, self.auto_update
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
        self.last_check = datetime.utcnow()
        remote = self._get_remote_sha()
        local = self._get_local_sha()
        self.last_remote_sha, self.last_local_sha = remote, local

        if not remote or not local:
            update_logger.warning(
                "SHA получить не удалось (remote=%s, local=%s)", remote, local
            )
            return False

        if remote == local:
            self.update_available = False
            update_logger.info("Обновлений нет (%s)", local[:7])
            return False

        self.update_available = True
        update_logger.warning("Доступно обновление: %s → %s",
                              local[:7], remote[:7])

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
    )
else:
    update_logger.warning(
        "Сервис обновлений не запущен: задайте GITHUB_REPO и GITHUB_TOKEN."
    )