"""The fixed ports: one server per port, and the same port again right after a restart (a
page left open reconnects only to the address it is looking at)."""
import threading
import urllib.request

import pytest

from wormscore.server import LocalServer
from http.server import BaseHTTPRequestHandler


class Hello(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"hi")


def serve(port=0):
    srv = LocalServer(("127.0.0.1", port), Hello)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def stop(srv):
    srv.shutdown()
    srv.server_close()


def test_a_second_server_cannot_share_the_port():
    srv = serve()
    try:
        with pytest.raises(OSError):
            LocalServer(("127.0.0.1", srv.server_address[1]), Hello)
    finally:
        stop(srv)


def test_the_port_is_free_again_right_after_a_restart():
    srv = serve()
    port = srv.server_address[1]
    for _ in range(3):        # the server closes these connections, leaving them in TIME_WAIT
        assert urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5).read() == b"hi"
    stop(srv)
    again = serve(port)       # a quit and reopen within a minute gets the same address
    stop(again)
