import ctypes as C
import unittest
from unittest import mock

import portal_dbus


class FakeGlib:
    def __init__(self):
        self.text = C.create_string_buffer(
            b"(uint32 0, {'uri': <'file:///tmp/sketchpad-shot.png'>})")

    def g_variant_type_new(self, _signature): return 2
    def g_variant_type_free(self, _value): pass
    def g_variant_parse(self, _type, _text, _limit, _end, _error): return 3
    def g_variant_get_child_value(self, _reply, _index): return 5
    def g_variant_get_string(self, _child, _length): return self.handle
    def g_variant_print(self, _parameters, _annotate): return C.addressof(self.text)
    def g_variant_unref(self, _value): pass
    def g_main_context_iteration(self, _context, _block): return 0
    def g_free(self, _value): pass


class FakeGio:
    def __init__(self, glib):
        self.glib = glib
        self.connection = None
        self.unsubscribed = False

    def g_bus_get_sync(self, _bus, _cancel, _error): return 1
    def g_dbus_connection_get_unique_name(self, connection):
        self.connection = connection
        return b":1.234"

    def g_dbus_connection_signal_subscribe(self, connection, _portal, _interface,
                                           _signal, path, _arg, _flags, callback, _data, _destroy):
        self.assert_connection(connection)
        self.path = path
        self.callback = callback
        return 7

    def g_dbus_connection_call_sync(self, connection, _portal, _object, _interface,
                                    _method, _args, _reply_type, _flags, _timeout, _cancel, _error):
        self.assert_connection(connection)
        self.glib.handle = self.path
        callback_type = C.CFUNCTYPE(None, C.c_void_p, C.c_char_p, C.c_char_p,
                                   C.c_char_p, C.c_char_p, C.c_void_p, C.c_void_p)
        callback_type(self.callback.value)(connection, None, self.path, None, None, 8, None)
        return 4

    def g_dbus_connection_signal_unsubscribe(self, connection, token):
        self.assert_connection(connection)
        self.unsubscribed = token == 7

    def assert_connection(self, connection):
        assert connection == self.connection


class PortalConnectionTest(unittest.TestCase):
    def test_request_and_response_use_one_connection(self):
        glib = FakeGlib()
        gio = FakeGio(glib)
        with mock.patch.object(portal_dbus, "_setup", return_value=(gio, glib, mock.Mock())):
            code, uri = portal_dbus.screenshot(False, 15)
        self.assertEqual((code, uri), (0, "file:///tmp/sketchpad-shot.png"))
        self.assertTrue(gio.unsubscribed)


if __name__ == "__main__":
    unittest.main()
