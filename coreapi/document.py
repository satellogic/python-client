# coding: utf-8
from __future__ import unicode_literals
from collections import OrderedDict, namedtuple
from coreapi.compat import string_types
from coreapi.exceptions import ErrorMessage
from coreapi.validation import validate_parameters
import itypes


def _to_immutable(value):
    if isinstance(value, dict):
        return Object(value)
    elif isinstance(value, list):
        return Array(value)
    return value


def _default_link_func(document, link, **parameters):
    """
    When calling a link the default behavior is to call through
    to the HTTP transport layer.
    """
    from coreapi.sessions import DefaultSession
    session = DefaultSession()
    return session.transition(link.url, link.action, parameters=parameters)


def _repr(node):
    from coreapi.codecs.python import PythonCodec
    return PythonCodec().dump(node)


def _str(node):
    from coreapi.codecs.plaintext import PlainTextCodec
    return PlainTextCodec().dump(node)


def _key_sorting(item):
    """
    Document and Object sorting.
    Regular attributes sorted alphabetically, then links sorted alphabetically.
    """
    key, value = item
    if isinstance(value, Link):
        return (1, key)
    return (0, key)


def dotted_path_to_list(doc, path):
    """
    Given a document and a string dotted notation like 'rows.123.edit",
    return a list of keys,such as ['rows', 123, 'edit'].
    """
    keys = path.split('.')
    active = doc
    for idx, key in enumerate(keys):
        # Coerce array lookups to integers.
        if isinstance(active, Array):
            try:
                key = int(key)
                keys[idx] = key
            except:
                pass

        # Descend through the document, so we can correctly identify
        # any nested array lookups.
        try:
            active = active[key]
        except (KeyError, IndexError, ValueError, TypeError):
            break
    return keys


# The field class, as used by Link objects:

Field = namedtuple('Field', ['name', 'required'])


def required(name):
    return Field(name, required=True)


# The Core API primatives:

class Document(itypes.Dict):
    """
    The Core API document type.

    Expresses the data that the client may access,
    and the actions that the client may perform.
    """

    def __init__(self, url=None, title=None, content=None):
        if title is None and content is None and isinstance(url, dict):
            # If a single positional argument is set and is a dictionary,
            # treat it as the document content.
            content = url
            url = None

        data = {} if (content is None) else content

        if url is not None and not isinstance(url, string_types):
            raise TypeError("'url' must be a string.")
        if title is not None and not isinstance(title, string_types):
            raise TypeError("'title' must be a string.")
        if content is not None and not isinstance(content, dict):
            raise TypeError("'content' must be a dict.")
        if any([not isinstance(key, string_types) for key in data.keys()]):
            raise TypeError('Document keys must be strings.')
        if any([not isinstance(value, primative_types) for value in data.values()]):
            raise TypeError('Document values must be primatives.')

        self._url = '' if (url is None) else url
        self._title = '' if (title is None) else title
        self._data = {key: _to_immutable(value) for key, value in data.items()}

    def clone(self, data):
        return Document(self.url, self.title, data)

    def __iter__(self):
        items = sorted(self._data.items(), key=_key_sorting)
        return iter([key for key, value in items])

    def __repr__(self):
        return _repr(self)

    def __str__(self):
        return _str(self)

    @property
    def url(self):
        return self._url

    @property
    def title(self):
        return self._title

    @property
    def data(self):
        return OrderedDict([
            (key, value) for key, value in self.items()
            if not isinstance(value, Link)
        ])

    @property
    def links(self):
        return OrderedDict([
            (key, value) for key, value in self.items()
            if isinstance(value, Link)
        ])

    def action(self, keys, **kwargs):
        """
        Perform an action by calling one of the links in the document tree.
        Returns a new document, or `None` if the current document was removed.
        """
        if isinstance(keys, string_types):
            keys = dotted_path_to_list(self, keys)

        if not isinstance(keys, (list, tuple)):
            msg = "'keys' must be a dot seperated string or a list of strings."
            raise TypeError(msg)
        if any([
            not isinstance(key, string_types) and not isinstance(key, int)
            for key in keys
        ]):
            raise TypeError("'keys' must be a list of strings or ints.")

        # Determine the link node being acted on, and its parent document.
        # 'node' is the link we're calling the action for.
        # 'document_keys' is the list of keys to the link's parent document.
        node = self
        document = self
        document_keys = []
        for idx, key in enumerate(keys, start=1):
            node = node[key]
            if isinstance(node, Document):
                document = node
                document_keys = keys[:idx]

        # Ensure that we've correctly indexed into a link.
        if not isinstance(node, Link):
            raise ValueError(
                "Can only call 'action' on a Link. Got type '%s'." % type(node)
            )
        link = node

        # Perform the action, and return a new document.
        ret = link._call(document, **kwargs)

        # If we got an error response back, raise an exception.
        if isinstance(ret, Error):
            raise ErrorMessage(ret.messages)

        # Return the new document or other media.
        transition = link.transition
        if not transition and link.action.lower() in ('put', 'patch', 'delete'):
            transition = 'inline'

        if transition == 'inline':
            if ret is None:
                return self.delete_in(document_keys)
            return self.set_in(document_keys, ret)
        return ret


class Object(itypes.Dict):
    """
    An immutable mapping of strings to values.
    """
    def __init__(self, *args, **kwargs):
        data = dict(*args, **kwargs)
        if any([not isinstance(key, string_types) for key in data.keys()]):
            raise TypeError('Object keys must be strings.')
        self._data = {key: _to_immutable(value) for key, value in data.items()}

    def __iter__(self):
        items = sorted(self._data.items(), key=_key_sorting)
        return iter([key for key, value in items])

    def __repr__(self):
        return _repr(self)

    def __str__(self):
        return _str(self)

    @property
    def data(self):
        return OrderedDict([
            (key, value) for key, value in self.items()
            if not isinstance(value, Link)
        ])

    @property
    def links(self):
        return OrderedDict([
            (key, value) for key, value in self.items()
            if isinstance(value, Link)
        ])


class Array(itypes.List):
    """
    An immutable list type container.
    """
    def __init__(self, *args):
        self._data = [_to_immutable(value) for value in list(*args)]

    def __repr__(self):
        return _repr(self)

    def __str__(self):
        return _str(self)


class Link(object):
    """
    Links represent the actions that a client may perform.
    """
    def __init__(self, url=None, action=None, transition=None, fields=None, func=None):
        if (url is not None) and (not isinstance(url, string_types)):
            raise TypeError("Argument 'url' must be a string.")
        if (action is not None) and (not isinstance(action, string_types)):
            raise TypeError("Argument 'action' must be a string.")
        if (transition is not None) and (not isinstance(transition, string_types)):
            raise TypeError("Argument 'transition' must be a string.")
        if (fields is not None) and (not isinstance(fields, list)):
            raise TypeError("Argument 'fields' must be a list.")
        if (fields is not None) and any([
            not (isinstance(item, string_types) or isinstance(item, Field))
            for item in fields
        ]):
            raise TypeError("Argument 'fields' must be a list of strings or fields.")

        self._url = '' if (url is None) else url
        self._action = '' if (action is None) else action
        self._transition = '' if (transition is None) else transition
        self._fields = () if (fields is None) else tuple([
            item if isinstance(item, Field) else Field(item, required=False)
            for item in fields
        ])
        self._func = _default_link_func if func is None else func

    @property
    def url(self):
        return self._url

    @property
    def action(self):
        return self._action

    @property
    def transition(self):
        return self._transition

    @property
    def fields(self):
        return self._fields

    def _call(self, document, **parameters):
        """
        Call a link and return a new document or other media.
        """
        validate_parameters(self, parameters)
        return self._func(document=document, link=self, **parameters)

    def __setattr__(self, key, value):
        if key.startswith('_'):
            return object.__setattr__(self, key, value)
        raise TypeError("'Link' object does not support property assignment")

    def __eq__(self, other):
        return (
            isinstance(other, Link) and
            self.url == other.url and
            self.action == other.action and
            self.transition == other.transition and
            set(self.fields) == set(other.fields)
        )

    def __repr__(self):
        return _repr(self)

    def __str__(self):
        return _str(self)


class Error(object):
    """
    Represents an error message or messages from a Core API interface.
    """
    def __init__(self, messages):
        if not isinstance(messages, (list, tuple)):
            raise TypeError("'messages' should be a list of strings.")
        if any([not isinstance(message, string_types) for message in messages]):
            raise TypeError("'messages' should be a list of strings.")

        self._messages = tuple(messages)

    @property
    def messages(self):
        return list(self._messages)

    def __setattr__(self, key, value):
        if key.startswith('_'):
            return object.__setattr__(self, key, value)
        raise TypeError("'Error' object does not support property assignment")

    def __eq__(self, other):
        if isinstance(other, Error):
            return self.messages == other.messages
        return self.messages == other

    def __repr__(self):
        return _repr(self)

    def __str__(self):
        return _str(self)


primative_types = string_types + (
    type(None), int, float, bool, list, dict,
    Document, Object, Array, Link
)
