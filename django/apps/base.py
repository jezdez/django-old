import re
import sys

from django.apps.options import AppOptions, DEFAULT_NAMES

module_name_re = re.compile(r'_([a-z])')


class AppBase(type):
    """
    Metaclass for all apps.
    """
    def __new__(cls, name, bases, attrs):
        super_new = super(AppBase, cls).__new__
        parents = [b for b in bases if isinstance(b, AppBase)]
        if not parents:
            # If this isn't a subclass of App, don't do anything special.
            return super_new(cls, name, bases, attrs)
        module = attrs.pop('__module__', None)
        new_class = super_new(cls, name, bases, {'__module__': module})
        attr_meta = attrs.pop('Meta', None)
        if not attr_meta:
            meta = getattr(new_class, 'Meta', None)
        else:
            meta = attr_meta
        app_name = attrs.pop('_name', None)
        if app_name is None:
            # Figure out the app_name by looking one level up.
            # For 'django.contrib.sites.app', this would be 'django.contrib.sites'
            app_module = sys.modules[new_class.__module__]
            app_name = app_module.__name__.rsplit('.', 1)[0]
        app_label = attrs.pop('_label', app_name.split('.')[-1])
        new_class.add_to_class('_meta', AppOptions(app_name, meta, app_label))
        # For easier Meta inheritance
        new_class.add_to_class('Meta', attr_meta)
        # Add all remaining attributes to the class.
        for obj_name, obj in attrs.items():
            new_class.add_to_class(obj_name, obj)
        return new_class

    def add_to_class(cls, name, value):
        if hasattr(value, 'contribute_to_class'):
            value.contribute_to_class(cls, name)
        else:
            setattr(cls, name, value)


class App(object):
    """
    The base app class to be subclassed for own uses.
    """
    __metaclass__ = AppBase

    def __init__(self, **options):
        for key, value in options.iteritems():
            if key in DEFAULT_NAMES:
                setattr(self._meta, key, value)
            else:
                setattr(self, key, value)

    def __repr__(self):
        return '<App: %s>' % self._meta.name

    @classmethod
    def from_name(cls, name, label):
        upper = lambda match: match.group(1).upper()
        cls_name = module_name_re.sub(upper, name.split('.')[-1])
        return type(cls_name[0].upper()+cls_name[1:], (cls,), {'_name': name, '_label': label})
