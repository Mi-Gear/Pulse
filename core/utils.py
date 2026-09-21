from flask_login import current_user
from models import Queue
from config import ALLOWED_EXTENSIONS
from typing import Callable, Dict, Any, List, Tuple, Optional
from dataclasses import dataclass, field
import asyncio

def can_manage_queue(user, queue):
    if user.is_admin:
        return True
    if queue and user in queue.admins:
        return True
    return False

def get_available_queues():
    if current_user.is_authenticated and current_user.is_admin:
        return Queue.query.filter_by(is_active=True).all()
    elif current_user.is_authenticated:
        return current_user.queues
    return []

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@dataclass
class Hook:
    name: str
    callbacks: List[Tuple[Callable, Dict[str, Any]]] = field(default_factory=list)
    
    def add_callback(self, callback: Callable, **kwargs):
        """Добавляет callback с параметрами"""
        self.callbacks.append((callback, kwargs))
    
    async def execute(self, *args, **kwargs):
        """Выполняет все callback с переданными параметрами"""
        results = []
        for callback, stored_kwargs in self.callbacks:
            try:
                merged_kwargs = {**stored_kwargs, **kwargs}
                if asyncio.iscoroutinefunction(callback):
                    result = await callback(**merged_kwargs)
                else:
                    result = callback(**merged_kwargs)
                results.append(result)
            except Exception as e:
                print(f"❌ Ошибка в хуке {self.name}: {e}")
                results.append(None)
        return results

class AppCore():
    def __init__(self, app):
        self._app = app
        self._hooks: Dict[str, Hook] = {}
    
    @property
    def config(self):
        return self._app.config
    
    @property
    def extensions(self):
        return self._app.extensions
    
    @property
    def teardown_appcontext(self):
        return self._app.teardown_appcontext
    
    @property
    def shell_context_processor(self):
        return self._app.shell_context_processor
    
    @property
    def instance_path(self):
        return self._app.instance_path
    
    @property
    def after_request(self):
        return self._app.after_request
    
    @property
    def context_processor(self):
        return self._app.context_processor
    
    @property
    def route(self):
        return self._app.route
    
    @property
    def app_context(self):
        return self._app.app_context
    @property
    def errorhandler(self):
        return self._app.errorhandler
    @property
    def logger(self):
        return self._app.logger
    @property
    def app(self):
        return self._app
    
    @property
    def run(self):
        return self._app.run
    
    @app.setter
    def app(self, value):
        self._app = value
    
    def register_blueprints(self, bp: list):
        for i in bp:
            self._app.register_blueprint(i, url_prefix='')
    
    def register_hook(self, name: str):
        """Создает хук с именем, если он не существует"""
        if name not in self._hooks:
            self._hooks[name] = Hook(name=name)
            print(f"✅ Хук '{name}' зарегистрирован")
        return self._hooks[name]
    
    def add_callback(self, name: str, callback: Callable, **kwargs):
        """Добавляет обработчик к хуку с параметрами"""
        if name not in self._hooks:
            self.register_hook(name)
        
        self._hooks[name].add_callback(callback, **kwargs)
        print(f"✅ Callback добавлен к хуку '{name}'")
        return True
    
    async def execute_hook(self, name: str, *args, **kwargs):
        """Выполняет все callback хука"""
        if name not in self._hooks:
            print(f"❌ Хук '{name}' не зарегистрирован. Пропускаем выполнение.")
            return []
        
        print(f"🔧 Выполнение хука '{name}' с параметрами: {kwargs}")
        return await self._hooks[name].execute(*args, **kwargs)
    
    def get_hook(self, name: str) -> Optional[Hook]:
        return self._hooks.get(name)
    
    def __iter__(self):
        return iter(self._hooks.keys())
    
    def __len__(self):
        return len(self._hooks)