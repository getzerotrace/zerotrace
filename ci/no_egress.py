"""Run a command with every non-loopback socket connection blocked.

ZeroTrace promises that detection never talks to the network and that the model client only
ever reaches its configured endpoint. This turns that promise into a build gate:

    python ci/no_egress.py -m pytest -q
"""
import runpy
import socket
import sys

_ALLOWED = {"127.0.0.1", "::1", "localhost", ""}


class EgressBlocked(RuntimeError):
    """Raised instead of opening a connection to anything but loopback."""


def _host_of(address) -> str:
    if isinstance(address, tuple) and address:
        return str(address[0])
    return str(address)


def _check(address) -> None:
    host = _host_of(address)
    if host not in _ALLOWED:
        raise EgressBlocked(f"outbound connection to {host} blocked by ci/no_egress.py")


def _wrap_method(original):
    def wrapper(self, address, *args, **kwargs):
        _check(address)
        return original(self, address, *args, **kwargs)
    return wrapper


def _wrap_function(original):
    def wrapper(address, *args, **kwargs):
        _check(address)
        return original(address, *args, **kwargs)
    return wrapper


def install() -> None:
    socket.socket.connect = _wrap_method(socket.socket.connect)
    socket.socket.connect_ex = _wrap_method(socket.socket.connect_ex)
    socket.create_connection = _wrap_function(socket.create_connection)


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] != "-m":
        raise SystemExit(__doc__)
    install()
    module, sys.argv = sys.argv[2], sys.argv[2:]
    runpy.run_module(module, run_name="__main__")
