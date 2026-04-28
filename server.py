#!/usr/bin/env python3
"""
server.py - Beginner-friendly TCP chat server.

This server routes encrypted payloads between clients and never decrypts
chat message content.
"""

import argparse
import json
import signal
import socket
import sys
import threading
from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass
class ClientSession:
    username: str
    public_key_b64: str


class ChatServer:
    """Threaded TCP chat router for end-to-end encrypted messages."""

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port

        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        self.clients_by_socket: Dict[socket.socket, ClientSession] = {}
        self.clients_by_username: Dict[str, socket.socket] = {}
        self.clients_lock = threading.Lock()

        self.running = False

    @staticmethod
    def _send_json(client_socket: socket.socket, payload: dict) -> None:
        client_socket.sendall((json.dumps(payload) + "\n").encode("utf-8"))

    @staticmethod
    def _try_parse_json(line: bytes) -> Optional[dict]:
        try:
            message = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return message if isinstance(message, dict) else None

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
                client_socket, client_address = self.server_socket.accept()
            except OSError:
                break

            thread = threading.Thread(
                target=self.handle_client,
                args=(client_socket, client_address),
                daemon=True,
            )
            thread.start()

        self.shutdown()

    def _send_notice(self, client_socket: socket.socket, text: str) -> None:
        self._send_json(client_socket, {"type": "server_notice", "text": text})

    def _broadcast_control(self, payload: dict, exclude: Optional[socket.socket] = None) -> None:
        dead_sockets = []
        with self.clients_lock:
            recipients = list(self.clients_by_socket.keys())

        for client_socket in recipients:
            if client_socket is exclude:
                continue
            try:
                self._send_json(client_socket, payload)
            except (BrokenPipeError, ConnectionResetError, OSError):
                dead_sockets.append(client_socket)

        for dead in dead_sockets:
            self.remove_client(dead)

    def _register_client(self, client_socket: socket.socket, hello: dict) -> tuple[bool, str]:
        username = str(hello.get("username", "")).strip() or "anonymous"
        public_key_b64 = str(hello.get("pubkey", "")).strip()

        if not public_key_b64:
            return False, "Missing public key in hello message."

        with self.clients_lock:
            if username in self.clients_by_username:
                return False, f"Username '{username}' is already in use."

            self.clients_by_socket[client_socket] = ClientSession(
                username=username,
                public_key_b64=public_key_b64,
            )
            self.clients_by_username[username] = client_socket
            peers = [
                {"username": session.username, "pubkey": session.public_key_b64}
                for sock, session in self.clients_by_socket.items()
                if sock is not client_socket
            ]

        self._send_json(client_socket, {"type": "roster", "peers": peers})
        self._send_notice(client_socket, "Welcome! Type /quit to leave.")

        self._broadcast_control(
            {
                "type": "peer_joined",
                "username": username,
                "pubkey": public_key_b64,
            },
            exclude=client_socket,
        )

        print(f"[SERVER] {username} joined")
        return True, username

    def _route_encrypted(self, payload: dict) -> None:
        recipient = str(payload.get("recipient", "")).strip()
        if not recipient:
            return

        with self.clients_lock:
            target_socket = self.clients_by_username.get(recipient)

        if target_socket is None:
            return

        try:
            self._send_json(target_socket, payload)
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.remove_client(target_socket)

    def handle_client(self, client_socket: socket.socket, client_address: Tuple[str, int]) -> None:
        """Receive control packets and route encrypted client-to-client messages."""
        del client_address
        username = "unknown"
        buffer = b""

        try:
            hello_line = b""
            while self.running and b"\n" not in buffer:
                chunk = client_socket.recv(4096)
                if not chunk:
                    client_socket.close()
                    return
                buffer += chunk

            hello_line, _, buffer = buffer.partition(b"\n")
            hello = self._try_parse_json(hello_line)
            if hello is None or hello.get("type") != "hello":
                self._send_notice(client_socket, "Protocol error: expected hello packet.")
                client_socket.close()
                return

            ok, result = self._register_client(client_socket, hello)
            if not ok:
                self._send_notice(client_socket, result)
                client_socket.close()
                return
            username = result

            while self.running:
                if b"\n" not in buffer:
                    chunk = client_socket.recv(4096)
                    if not chunk:
                        break
                    buffer += chunk
                    continue

                raw_line, _, buffer = buffer.partition(b"\n")
                if not raw_line.strip():
                    continue

                message = self._try_parse_json(raw_line)
                if message is None:
                    continue

                if message.get("type") == "encrypted":
                    self._route_encrypted(message)

        except (ConnectionResetError, BrokenPipeError):
            pass
        except OSError as exc:
            if self.running:
                print(f"[SERVER] Client error for {username}: {exc}")
        finally:
            self.remove_client(client_socket)

    def remove_client(self, client_socket: socket.socket) -> None:
        """Remove client safely and announce departure once."""
        with self.clients_lock:
            session = self.clients_by_socket.pop(client_socket, None)
            if session is not None:
                self.clients_by_username.pop(session.username, None)

        try:
            client_socket.close()
        except OSError:
            pass

        if session:
            print(f"[SERVER] {session.username} left the chat")
            self._broadcast_control({"type": "peer_left", "username": session.username})

    def shutdown(self) -> None:
        """Close all sockets and stop server safely."""
        if not self.running:
            try:
                self.server_socket.close()
            except OSError:
                pass
            return

        self.running = False
        print("\n[SERVER] Shutting down...")

        with self.clients_lock:
            sockets = list(self.clients_by_socket.keys())
            self.clients_by_socket.clear()
            self.clients_by_username.clear()

        for client_socket in sockets:
            try:
                self._send_notice(client_socket, "Server is shutting down.")
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
