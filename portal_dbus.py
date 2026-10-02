"""A single D-Bus connection for desktop portal requests, using system libgio.

The portal sends Request.Response only to the connection that called Screenshot.
Two separate gdbus CLI processes cannot receive that unicast response. ctypes keeps
this stdlib-only while using the same libgio that /usr/bin/gdbus uses.
"""
import ctypes as C
import ctypes.util
import re
import secrets
import time

PORTAL = b"org.freedesktop.portal.Desktop"
OBJECT = b"/org/freedesktop/portal/desktop"
SCREENSHOT = b"org.freedesktop.portal.Screenshot"
REQUEST = b"org.freedesktop.portal.Request"
URI = re.compile(r"'uri': <'([^']+)'>")


class GError(C.Structure):
    _fields_ = [("domain", C.c_uint), ("code", C.c_int), ("message", C.c_char_p)]


def _lib(name):
    path = ctypes.util.find_library(name)
    if not path:
        raise ValueError(f"desktop portal requires lib{name}")
    return C.CDLL(path)


def _setup():
    gio, glib, gobject = _lib("gio-2.0"), _lib("glib-2.0"), _lib("gobject-2.0")
    def api(lib, name, restype, *args):
        func = getattr(lib, name)
        func.restype, func.argtypes = restype, args
        return func
    p, s, i, u = C.c_void_p, C.c_char_p, C.c_int, C.c_uint
    api(gio, "g_bus_get_sync", p, i, p, C.POINTER(p))
    api(gio, "g_dbus_connection_get_unique_name", s, p)
    api(gio, "g_dbus_connection_signal_subscribe", u, p, s, s, s, s, s, i, p, p, p)
    api(gio, "g_dbus_connection_signal_unsubscribe", None, p, u)
    api(gio, "g_dbus_connection_call_sync", p, p, s, s, s, s, p, p, i, i, p, C.POINTER(p))
    api(glib, "g_variant_type_new", p, s)
    api(glib, "g_variant_type_free", None, p)
    api(glib, "g_variant_parse", p, p, s, p, p, C.POINTER(p))
    api(glib, "g_variant_get_child_value", p, p, u)
    api(glib, "g_variant_get_string", s, p, p)
    api(glib, "g_variant_print", p, p, i)
    api(glib, "g_variant_unref", None, p)
    api(glib, "g_main_context_iteration", i, p, i)
    api(glib, "g_error_free", None, p)
    api(glib, "g_free", None, p)
    api(gobject, "g_object_unref", None, p)
    return gio, glib, gobject


def _error(glib, pointer, fallback):
    if pointer:
        message = C.cast(pointer, C.POINTER(GError)).contents.message.decode(errors="replace")
        glib.g_error_free(pointer)
        return message
    return fallback


def screenshot(interactive, timeout):
    """Return (response code, file URI) from the screenshot portal."""
    deadline = time.monotonic() + timeout
    gio, glib, gobject = _setup()
    error = C.c_void_p()
    connection = gio.g_bus_get_sync(2, None, C.byref(error))  # G_BUS_TYPE_SESSION
    if not connection:
        raise ValueError("desktop session bus unavailable: " + _error(glib, error, "unknown error"))
    subscription = 0
    try:
        unique = gio.g_dbus_connection_get_unique_name(connection).decode()
        sender = unique[1:].replace(".", "_")
        token = "sketchpad_" + secrets.token_hex(12)
        handle = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        seen = []
        callback_type = C.CFUNCTYPE(None, C.c_void_p, C.c_char_p, C.c_char_p,
                                   C.c_char_p, C.c_char_p, C.c_void_p, C.c_void_p)

        @callback_type
        def on_response(_conn, _sender, path, _interface, _signal, parameters, _data):
            if path.decode() == handle:
                printed = glib.g_variant_print(parameters, 1)
                try:
                    seen.append(C.string_at(printed).decode(errors="replace"))
                finally:
                    glib.g_free(printed)

        subscription = gio.g_dbus_connection_signal_subscribe(
            connection, PORTAL, REQUEST, b"Response", handle.encode(), None, 0,
            C.cast(on_response, C.c_void_p), None, None)
        variant_type = glib.g_variant_type_new(b"(sa{sv})")
        try:
            args = f"('', {{'interactive': <{'true' if interactive else 'false'}>, 'handle_token': <'{token}'>}})"
            parameters = glib.g_variant_parse(variant_type, args.encode(), None, None, C.byref(error))
        finally:
            glib.g_variant_type_free(variant_type)
        if not parameters:
            raise ValueError("could not build portal request: " + _error(glib, error, "invalid arguments"))
        reply = gio.g_dbus_connection_call_sync(
            connection, PORTAL, OBJECT, SCREENSHOT, b"Screenshot", parameters,
            None, 0, min(timeout, 10) * 1000, None, C.byref(error))
        if not reply:
            raise ValueError("screenshot portal failed: " + _error(glib, error, "request rejected"))
        try:
            child = glib.g_variant_get_child_value(reply, 0)
            try:
                returned = glib.g_variant_get_string(child, None).decode()
            finally:
                glib.g_variant_unref(child)
        finally:
            glib.g_variant_unref(reply)
        if returned != handle:
            raise ValueError("screenshot portal returned an unexpected request handle")
        while not seen and time.monotonic() < deadline:
            glib.g_main_context_iteration(None, 0)
            if not seen:
                time.sleep(.01)
        if not seen:
            raise ValueError("screenshot timed out")
        match = re.search(r"uint32 (\d+)", seen[0])
        if not match:
            raise ValueError("screenshot portal returned an invalid response")
        uri = URI.search(seen[0])
        return int(match.group(1)), uri.group(1) if uri else None
    finally:
        if subscription:
            gio.g_dbus_connection_signal_unsubscribe(connection, subscription)
        gobject.g_object_unref(connection)
