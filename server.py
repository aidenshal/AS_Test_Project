#!/usr/bin/env python3
"""
server.py - Beginner-friendly TCP chat server.

This file creates a TCP server that accepts many client connections and
broadcasts messages between them.
"""

import argparse
import signal
import socket
import sys
import threading
from typing import Dict, Optional, Tuple

# ------------------------------
# Basic networking terms (quick notes)
# ------------------------------
# IP address: identifies a machine on a network (for local machine, 127.0.0.1).
# Port: identifies a specific app/service on that machine (example: 5000).
# TCP: reliable, connection-based protocol. Good for chat because messages
#      arrive in order and with delivery checks.
# localhost: special host name/IP that means "this same computer".
#
# Socket: a programming object that lets programs send/receive data on a network.
# - Server socket: waits for incoming connections.
# - Client socket: connects to a server and exchanges data.
#
# Important socket methods:
# - bind((host, port)): attach server socket to a network address.
# - listen(): mark server socket as ready to accept incoming connections.
# - accept(): wait for an incoming connection; returns (client_socket, address).
# - connect((host, port)): used by client socket to reach server.
# - send()/sendall(): send bytes to the other side.
# - recv(size): receive bytes from the other side.


class ChatServer:
    """Simple multi-client TCP chat server using threads."""

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port

        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Reuse address quickly after restart so "Address already in use" is less likely.
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # Track connected clients: socket -> username
        self.clients: Dict[socket.socket, str] = {}
        self.clients_lock = threading.Lock()

        self.running = False

    def start(self) -> None:
        """Bind, listen, and continuously accept clients."""
        try:
            self.server_socket.bind((self.host, self.port))
            self.server_socket.listen()
        except OSError as exc:
            print(f"[SERVER] Failed to start on {self.host}:{self.port} -> {exc}")
            return

        self.running = True
        print(f"[SERVER] Listening on {self.host}:{self.port}")
        print("[SERVER] Press Ctrl+C to stop the server.")

        while self.running:
            try:
                # accept() blocks until a client connects.
                client_socket, client_address = self.server_socket.accept()
            except OSError:
                # Happens when socket is closed during shutdown.
                break

            # Why threading?
            # Each client can block on recv(). If we handled clients one by one
            # without threads, one slow client could freeze everyone else.
            # A thread per client lets all users chat concurrently.
            thread = threading.Thread(
                target=self.handle_client,
                args=(client_socket, client_address),
                daemon=True,
            )
            thread.start()

        self.shutdown()

    def broadcast(self, message: str, exclude: Optional[socket.socket] = None) -> None:
        """Send a message to all connected clients except optional exclude socket."""
        dead_sockets = []

        with self.clients_lock:
            recipients = list(self.clients.keys())

        for client_socket in recipients:
            if client_socket is exclude:
                continue
            try:
                client_socket.sendall(message.encode("utf-8"))
            except (BrokenPipeError, ConnectionResetError, OSError):
                # Client is gone; schedule removal.
                dead_sockets.append(client_socket)

        for dead in dead_sockets:
            self.remove_client(dead)

    def handle_client(self, client_socket: socket.socket, client_address: Tuple[str, int]) -> None:
        """Receive username, then relay chat messages from this client."""
        username = "unknown"
        try:
            # First message from client is username.
            username_data = client_socket.recv(1024)
            if not username_data:
                client_socket.close()
                return

            username = username_data.decode("utf-8").strip() or "anonymous"

            with self.clients_lock:
                self.clients[client_socket] = username

            print(f"[SERVER] {username} joined from {client_address[0]}:{client_address[1]}")
            self.broadcast(f"[SERVER] {username} has joined the chat.\n", exclude=client_socket)
            client_socket.sendall(b"[SERVER] Welcome! Type /quit to leave.\n")

            while self.running:
                data = client_socket.recv(1024)
                if not data:
                    # Client disconnected normally.
                    break

                text = data.decode("utf-8").strip()
                if not text:
                    continue

                full_message = f"[{username}] {text}\n"
                print(full_message, end="")
                self.broadcast(full_message, exclude=client_socket)

        except (ConnectionResetError, BrokenPipeError):
            # Common network disconnect errors; handle gracefully.
            pass
        except OSError as exc:
            if self.running:
                print(f"[SERVER] Client error for {username}: {exc}")
        finally:
            self.remove_client(client_socket)

    def remove_client(self, client_socket: socket.socket) -> None:
        """Remove client safely and announce departure once."""
        with self.clients_lock:
            username = self.clients.pop(client_socket, None)

        try:
            client_socket.close()
        except OSError:
            pass

        if username:
            print(f"[SERVER] {username} left the chat")
            self.broadcast(f"[SERVER] {username} has left the chat.\n", exclude=None)

    def shutdown(self) -> None:
        """Close all sockets and stop server safely."""
        if not self.running:
            # Invalid shutdown case: already stopped. Keep behavior safe.
            try:
                self.server_socket.close()
            except OSError:
                pass
            return

        self.running = False
        print("\n[SERVER] Shutting down...")

        with self.clients_lock:
            sockets = list(self.clients.keys())
            self.clients.clear()

        for client_socket in sockets:
            try:
                client_socket.sendall(b"[SERVER] Server is shutting down.\n")
            except OSError:
                pass
            try:
                client_socket.close()
            except OSError:
                pass

        try:
            self.server_socket.close()
        except OSError:
            pass

        print("[SERVER] Shutdown complete.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Beginner TCP chat server")
    parser.add_argument("--host", default="127.0.0.1", help="Host/IP to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5000, help="Port to listen on (default: 5000)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    server = ChatServer(args.host, args.port)

    def handle_signal(signum, frame):  # type: ignore[no-untyped-def]
        del signum, frame
        server.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    server.start()


if __name__ == "__main__":
    main()
