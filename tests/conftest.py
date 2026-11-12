"""Keep unit and API tests free of actual network and background I/O."""
import socket

import pytest


@pytest.fixture(autouse=True)
def isolated_background_workers(monkeypatch, request, tmp_path):
    # Intentionally live integration tests must opt in explicitly.
    if request.node.get_closest_marker("external_network"):
        return

    from app import main, online_library
    # Moderation persistence must never leak between independent tests.
    monkeypatch.setattr(online_library, "SELECTION_FILE", tmp_path / "selection.json")

    def idle(stop):
        stop.wait()

    # The app's real pollers are not part of a TestClient's API tests.
    # Tests of a particular poller can explicitly override this fixture.
    monkeypatch.setattr(main, "_online_loop", idle)
    monkeypatch.setattr(main.weather, "polling_loop", idle)
    monkeypatch.setattr(main.local_videos, "polling_loop", idle)
    monkeypatch.setattr(main.github_updates, "polling_loop", idle)
    # Direct API actions may schedule cache work even without the poller.
    # Tests of warm_cache explicitly replace try_cache with their own spy.
    monkeypatch.setattr(main.online, "try_cache", lambda entry, stop=None: None)

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def no_external_tcp(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError("Unit test attempted a real network connection")
        return original_connect(sock, address)

    def no_external_tcp_ex(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError("Unit test attempted a real network connection")
        return original_connect_ex(sock, address)

    monkeypatch.setattr(socket.socket, "connect", no_external_tcp)
    monkeypatch.setattr(socket.socket, "connect_ex", no_external_tcp_ex)
