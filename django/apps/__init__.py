from django.apps.base import App
from django.apps.cache import AppCache

__all__ = (
    'App', 'find_app', 'get_apps', 'get_app', 'get_app_errors',
    'get_models', 'get_model', 'register_models', 'load_app',
    'app_cache_ready'
)

cache = AppCache()

# These methods were always module level, so are kept that way for backwards
# compatibility.
find_app = cache.find_app
get_apps = cache.get_apps
get_app = cache.get_app
get_app_errors = cache.get_app_errors
get_models = cache.get_models
get_model = cache.get_model
register_models = cache.register_models
load_app = cache.load_app
app_cache_ready = cache.app_cache_ready
get_instances = cache.get_instances
