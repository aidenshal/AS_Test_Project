#!/usr/bin/env python3
"""
client.py - Beginner-friendly TCP chat client.

This file connects to a TCP chat server, sends user input,
and prints incoming messages in real time.
"""

import argparse
import socket
import threading


# Why threading on the client?
# - Receiving data with recv() can block (wait).
# - Sending input() also blocks waiting for user typing.
# If one thread handles recv and another handles input/send,
# the user can type while messages still appear live.


def receive_messages(client_socket: socket.socket, stop_event: threading.Event) -> None:
    """Continuously receive and print server/chat messages."""
    while not stop_event.is_set():
        try:
            data = client_socket.recv(1024)
            if not data:
                print("\n[INFO] Disconnected: server closed the connection.")
                stop_event.set()
                break

            print(data.decode("utf-8"), end="")
        except ConnectionResetError:
            print("\n[INFO] Connection reset by server.")
            stop_event.set()
            break
        except OSError:
            # Socket likely closed locally during /quit.
            stop_event.set()
            break


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Beginner TCP chat client")
    parser.add_argument("--host", default="127.0.0.1", help="Server host/IP (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5000, help="Server port (default: 5000)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    username = input("Choose a username: ").strip()
    if not username:
        username = "anonymous"

    # Create a TCP client socket.
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        # connect() asks OS/network to establish a TCP connection to server.
        client_socket.connect((args.host, args.port))
    except ConnectionRefusedError:
        print(f"[ERROR] Connection refused. Is the server running on {args.host}:{args.port}?")
        client_socket.close()
        return
    except OSError as exc:
        print(f"[ERROR] Could not connect: {exc}")
        client_socket.close()
        return

    try:
        # First send username so server can identify this client.
        client_socket.sendall((username + "\n").encode("utf-8"))
    except OSError as exc:
        print(f"[ERROR] Failed to send username: {exc}")
        client_socket.close()
        return

    stop_event = threading.Event()
    receiver_thread = threading.Thread(
        target=receive_messages,
        args=(client_socket, stop_event),
        daemon=True,
    )
    receiver_thread.start()

    print("[INFO] Connected! Type messages and press Enter to send.")
    print("[INFO] Type /quit to leave the chat.")

    while not stop_event.is_set():
        try:
            message = input()
        except EOFError:
            message = "/quit"
        except KeyboardInterrupt:
            message = "/quit"
            print()

        if message.strip() == "/quit":
            stop_event.set()
            try:
                client_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                client_socket.close()
            except OSError:
                pass
            print("[INFO] You disconnected.")
            break

        if not message.strip():
            continue

        try:
            client_socket.sendall((message + "\n").encode("utf-8"))
        except (BrokenPipeError, ConnectionResetError):
            print("[ERROR] Connection lost while sending message.")
            stop_event.set()
            break
        except OSError as exc:
            print(f"[ERROR] Send failed: {exc}")
            stop_event.set()
            break


if __name__ == "__main__":
    main()
