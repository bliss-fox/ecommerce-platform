import socket

import pytest


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    original_connect = socket.socket.connect
    original_create_connection = socket.create_connection

    def local_connect(sock, address):
        if address[0] not in ("127.0.0.1", "::1"):
            raise AssertionError("External network access forbidden")
        return original_connect(sock, address)

    def local_create_connection(address, *args, **kwargs):
        if address[0] not in ("127.0.0.1", "::1"):
            raise AssertionError("External network access forbidden")
        return original_create_connection(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", local_connect)
    monkeypatch.setattr(socket, "create_connection", local_create_connection)
