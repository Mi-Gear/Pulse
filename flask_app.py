from core.utils import AppCore
from flask import Flask

app = Flask("app")
app_core = AppCore(app)