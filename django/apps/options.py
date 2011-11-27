import re
import os

from django.utils.datastructures import SortedDict
from django.utils.importlib import import_module
from django.utils.text import get_verbose_name

DEFAULT_NAMES = ('verbose_name', 'db_prefix', 'models_path')


class AppOptions(object):
    def __init__(self, name, meta, label):
        self.name = name
        self.meta = meta
        self.label = label
        self.errors = []
        self.models = SortedDict()

    def contribute_to_class(self, cls, name):
        cls._meta = self
        self.models_module = None
        self.module = import_module(self.name)
        self.path = os.path.dirname(self.module.__file__)
        defaults = {
            'db_prefix': self.label,
            'models_path': '%s.models' % self.name,
            'verbose_name': get_verbose_name(self.label),
        }
        # Next, apply any overridden values from 'class Meta'.
        if self.meta:
            meta_attrs = self.meta.__dict__.copy()
            for name in self.meta.__dict__:
                # Ignore any private attributes that Django doesn't care about.
                if name.startswith('_'):
                    del meta_attrs[name]
            for attr_name in DEFAULT_NAMES:
                if attr_name in meta_attrs:
                    setattr(self, attr_name, meta_attrs.pop(attr_name))
                elif hasattr(self.meta, attr_name):
                    setattr(self, attr_name, getattr(self.meta, attr_name))
            # Any leftover attributes must be invalid.
            if meta_attrs != {}:
                raise TypeError("'class Meta' got invalid attribute(s): %s"
                                % ','.join(meta_attrs.keys()))
        del self.meta

        for k, v in defaults.iteritems():
            if not hasattr(self, k):
                setattr(self, k, v)
