# modules/__init__.py
import os
import importlib
import sys

modules_dir = os.path.dirname(__file__)
package_name = os.path.basename(modules_dir)  # 'modules'

__all__ = []

for filename in os.listdir(modules_dir):
    if filename.endswith('.py') and filename != '__init__.py':
        module_name = filename[:-3]
        
        try:
            # Пытаемся импортировать только одним способом
            # Относительный импорт - предпочтительный вариант
            module = importlib.import_module(f'.{module_name}', package=__name__)
            
            # Добавляем все публичные атрибуты
            for attr_name in dir(module):
                if not attr_name.startswith('_'):
                    # Проверяем, не добавлен ли уже этот атрибут
                    if attr_name not in globals():
                        globals()[attr_name] = getattr(module, attr_name)
                        if attr_name not in __all__:
                            __all__.append(attr_name)
                    
            print(f"✅ Импортирован: {module_name}")
            
        except ImportError as e:
            print(f"❌ Ошибка импорта {module_name}: {e}")