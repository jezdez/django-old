import sys
import os
import threading

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.datastructures import SortedDict
from django.utils.importlib import import_module
from django.utils.module_loading import module_has_submodule

from django.apps.base import App
from django.apps.signals import app_loaded, post_apps_loaded


def _initialize():
    """
    Returns a dictionary to be used as the initial value of the
    shared state of the app cache.
    """
    return {
        # list of loaded app instances
        'loaded_apps': [],

        # Mapping of app_labels to a dictionary of model names to model code.
        'app_models': SortedDict(),

        # -- Everything below here is only used when populating the cache --
        'loaded': False,
        'handled': [],
        'postponed': [],
        'nesting_level': 0,
        '_get_models_cache': {},
    }


class AppCache(object):
    """
    A cache that stores installed applications and their models. Used to
    provide reverse-relations and for app introspection (e.g. admin).
    """
    # Use the Borg pattern to share state between all instances. Details at
    # http://aspn.activestate.com/ASPN/Cookbook/Python/Recipe/66531.
    __shared_state = dict(_initialize(), write_lock=threading.RLock())

    def __init__(self):
        self.__dict__ = self.__shared_state

    def _reset(self):
        """
        Resets the cache to its initial (unseeded) state
        """
        # remove imported model modules, so ModelBase.__new__ can register
        # them with the cache again
        for app, models in self.app_models.iteritems():
            for model in models.itervalues():
                module = model.__module__
                if module in sys.modules:
                    del sys.modules[module]
        self.__class__.__shared_state.update(_initialize())

    def _reload(self):
        """
        Reloads the cache
        """
        self._reset()
        self._populate()

    def _populate(self):
        """
        Fill in all the cache information. This method is threadsafe, in the
        sense that every caller will see the same state upon return, and if the
        cache is already initialised, it does no work.
        """
        if self.loaded:
            return
        self.write_lock.acquire()
        try:
            if self.loaded:
                return
            for app_name in settings.INSTALLED_APPS:
                if isinstance(app_name, (tuple, list)):
                    app_name, app_kwargs = app_name
                else:
                    app_kwargs = {}
                self.load_app(app_name, app_kwargs, True)
            if not self.nesting_level:
                for app_name, app_kwargs in self.postponed:
                    self.load_app(app_name, app_kwargs)
                # assign models to app instances
                for app in self.loaded_apps:
                    parents = [p for p in app.__class__.mro()
                               if hasattr(p, '_meta')]
                    for parent in reversed(parents):
                        parent_models = self.app_models.get(parent._meta.label, {})
                        # update app_label and installed attribute of parent models
                        for model in parent_models.itervalues():
                            model._meta.app_label = app._meta.label
                            model._meta.installed = True
                        app._meta.models.update(parent_models)

                # check if there is more than one app with the same
                # db_prefix attribute
                for app1 in self.loaded_apps:
                    for app2 in self.loaded_apps:
                        if (app1 != app2 and
                                app1._meta.db_prefix == app2._meta.db_prefix):
                            raise ImproperlyConfigured(
                                'The apps "%s" and "%s" have the same db_prefix "%s"'
                                % (app1, app2, app1._meta.db_prefix))
                self.loaded = True
                # send the post_apps_loaded signal
                post_apps_loaded.send(sender=self, apps=self.loaded_apps)
        finally:
            self.write_lock.release()

    def get_app_class(self, app_name, app_label):
        """
        Returns an app class for the given app name, which can be a
        dotted path to an app class or a dotted app module path.
        """
        try:
            app_path, app_attr = app_name.rsplit('.', 1)
        except ValueError:
            # First, return a new app class for the given module if
            # it's one level module path that can't be rsplit (e.g. 'myapp')
            return App.from_name(app_name, app_label)
        try:
            # Secondly, try to import the module directly,
            # because it'll fail with a class path or a bad path
            app_module = import_module(app_path)
        except ImportError, e:
            raise ImproperlyConfigured(
                "Could not import app '%s': %s" % (app_path, e))
        else:
            # Thirdly, check if there is the submodule and fall back if yes
            # If not look for the app class and do some type checks
            if not module_has_submodule(app_module, app_attr):
                try:
                    app_class = getattr(app_module, app_attr)
                except AttributeError:
                    raise ImproperlyConfigured(
                        "Could not find app '%s' in "
                        "module '%s'" % (app_attr, app_path))
                else:
                    if not issubclass(app_class, App):
                        raise ImproperlyConfigured(
                            "App '%s' must be a subclass of "
                            "'django.apps.App'" % app_name)
                    return app_class
        return App.from_name(app_name, app_label)

    def load_app(self, app_name, app_kwargs=None, can_postpone=False):
        """
        Loads the app with the provided fully qualified name, and returns the
        model module.

        Keyword Arguments:
            app_name: fully qualified name (e.g. 'django.contrib.auth')
            can_postpone: If set to True and the import raises an ImportError
                the loading will be postponed and tried again when all other
                modules are loaded.
        """
        if app_kwargs is None:
            app_kwargs = {}

        self.handled.append(app_name)
        self.nesting_level += 1

        # check if an app instance with app_name already exists, if not
        # then create one
        app_label = app_kwargs.get('label', app_name.split('.')[-1])
        app = self.find_app(app_label)
        if not app:
            app_class = self.get_app_class(app_name, app_label)
            app = app_class(**app_kwargs)
            self.loaded_apps.append(app)
            # Send the signal that the app has been loaded
            app_loaded.send(sender=app_class, app=app)

        # import the app's models module and handle ImportErrors
        try:
            models = import_module(app._meta.models_path)
        except ImportError:
            self.nesting_level -= 1
            # If the app doesn't have a models module, we can just ignore the
            # ImportError and return no models for it.
            if not module_has_submodule(app._meta.module, 'models'):
                return None
            # But if the app does have a models module, we need to figure out
            # whether to suppress or propagate the error. If can_postpone is
            # True then it may be that the package is still being imported by
            # Python and the models module isn't available yet. So we add the
            # app to the postponed list and we'll try it again after all the
            # recursion has finished (in populate). If can_postpone is False
            # then it's time to raise the ImportError.
            else:
                if can_postpone:
                    self.postponed.append((app_name, app_kwargs))
                    return None
                else:
                    raise

        self.nesting_level -= 1
        app._meta.models_module = models
        return models

    def find_app(self, app_label):
        """
        Returns the app instance that matches the given label.
        """
        for app in self.loaded_apps:
            if app._meta.label == app_label:
                return app

    def find_app_by_models_module(self, models_module):
        """
        Returns the app instance that matches the models module
        """
        for app in self.loaded_apps:
            if app._meta.models_module == models_module:
                return app
    
    def get_instances(self, app_name):
        apps = []
        for app in self.loaded_apps:
            if app._meta.name == app_name:
                apps.append(app)
        return apps

    def app_cache_ready(self):
        """
        Returns true if the model cache is fully populated.

        Useful for code that wants to cache the results of get_models() for
        themselves once it is safe to do so.
        """
        return self.loaded

    def get_apps(self):
        """
        Returns a list of all models modules.
        """
        self._populate()
        return [app._meta.models_module for app in self.loaded_apps
                if app._meta.models_module]

    def get_app(self, app_label, emptyOK=False):
        """
        Returns the module containing the models for the given app_label. If
        the app has no models in it and 'emptyOK' is True, returns None.
        """
        self._populate()
        app = self.find_app(app_label)
        if app:
            mod = app._meta.models_module
            if mod is None:
                if emptyOK:
                    return None
            else:
                return mod
        raise ImproperlyConfigured(
                "App with label %s could not be found" % app_label)

    def get_app_errors(self):
        """
        Returns the map of known problems with the INSTALLED_APPS.
        """
        self._populate()
        errors = {}
        for app in self.loaded_apps:
            if app._meta.errors:
                errors.update({app._meta.label: app._meta.errors})
        return errors

    def get_models(self, app_mod=None,
                   include_auto_created=False, include_deferred=False,
                   only_installed=True):
        """
        Given a module containing models, returns a list of the models.
        Otherwise returns a list of all installed models.

        By default, auto-created models (i.e., m2m models without an
        explicit intermediate table) are not included. However, if you
        specify include_auto_created=True, they will be.

        By default, models created to satisfy deferred attribute
        queries are *not* included in the list of models. However, if
        you specify include_deferred, they will be.
        """
        cache_key = (app_mod, include_auto_created, include_deferred,
                     only_installed)
        try:
            return self._get_models_cache[cache_key]
        except KeyError:
            pass
        self._populate()
        app_list = []
        if app_mod and only_installed:
            app_label = app_mod.__name__.split('.')[-2]
            app = self.find_app(app_label)
            if app:
                app_list = [app._meta.models]
        else:
            if only_installed:
                app_list = [app._meta.models for app in self.loaded_apps]
            else:
                app_list = self.app_models.itervalues()
        model_list = []
        for app in app_list:
            model_list.extend(
                model for model in app.values()
                if ((not model._deferred or include_deferred) and
                    (not model._meta.auto_created or include_auto_created))
            )
        self._get_models_cache[cache_key] = model_list
        return model_list

    def get_model(self, app_label, model_name,
                  seed_cache=True, only_installed=True):
        """
        Returns the model matching the given app_label and case-insensitive
        model_name.

        Returns None if no model is found.
        """
        if seed_cache:
            self._populate()
        if only_installed:
            app = self.find_app(app_label)
            if not app:
                return
            return app._meta.models.get(model_name.lower())
        return self.app_models.get(app_label, SortedDict()).get(model_name.lower())

    def register_models(self, app_label, *models):
        """
        Register a set of models as belonging to an app.
        """
        app = self.find_app(app_label)
        for model in models:
            model_name = model._meta.object_name.lower()
            model_dict = self.app_models.setdefault(app_label, SortedDict())
            if model_name in model_dict:
                # The same model may be imported via different paths (e.g.
                # appname.models and project.appname.models). We use the source
                # filename as a means to detect identity.
                fname1 = os.path.abspath(sys.modules[model.__module__].__file__)
                fname2 = os.path.abspath(sys.modules[model_dict[model_name].__module__].__file__)
                # Since the filename extension could be .py the first time and
                # .pyc or .pyo the second time, ignore the extension when
                # comparing.
                if os.path.splitext(fname1)[0] == os.path.splitext(fname2)[0]:
                    continue
            if app:
                app._meta.models[model_name] = model
            model_dict[model_name] = model
        self._get_models_cache.clear()
