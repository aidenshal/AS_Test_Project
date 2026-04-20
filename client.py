#!/usr/bin/env python3
"""
client.py - Beginner-friendly TCP chat client.

This client now supports two experiences:
1) Classic terminal chat (default behavior in non-GUI environments)
2) A modern Tkinter desktop UI with quick actions and quality-of-life features
"""

import argparse
import base64
import json
import os
import queue
import socket
import threading
import time
from datetime import datetime

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext
except Exception:  # pragma: no cover - tkinter may not be available on some systems
    tk = None
    filedialog = None
    messagebox = None
    scrolledtext = None


def _derive_key(shared_secret: bytes, user_a: str, user_b: str) -> bytes:
    pair_label = "|".join(sorted([user_a, user_b])).encode("utf-8")
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"neon-chat-dh-salt-v1",
        info=b"neon-chat-e2e:" + pair_label,
    ).derive(shared_secret)


class ChatConnection:
    """Socket wrapper shared by terminal and GUI clients."""

    def __init__(self, host: str, port: int, username: str) -> None:
        self.host = host
        self.port = port
        self.username = username.strip() or "anonymous"

        self.socket: socket.socket | None = None
        self.stop_event = threading.Event()
        self.incoming: "queue.Queue[str]" = queue.Queue()
        self.receiver_thread: threading.Thread | None = None

        self._buffer = b""
        self.peers: dict[str, str] = {}
        self.peers_lock = threading.Lock()

        self.private_key: x25519.X25519PrivateKey | None = None
        self.public_key_b64 = ""

    @staticmethod
    def _send_json(sock: socket.socket, payload: dict) -> None:
        sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))

    @staticmethod
    def _decode_public_key(pubkey_b64: str) -> x25519.X25519PublicKey:
        raw = base64.b64decode(pubkey_b64.encode("utf-8"))
        return x25519.X25519PublicKey.from_public_bytes(raw)

    def connect(self) -> None:
        self.private_key = x25519.X25519PrivateKey.generate()
        public_key = self.private_key.public_key()
        self.public_key_b64 = base64.b64encode(
            public_key.public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
        ).decode("utf-8")

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.connect((self.host, self.port))
        self._send_json(
            self.socket,
            {
                "type": "hello",
                "username": self.username,
                "pubkey": self.public_key_b64,
            },
        )

        self.receiver_thread = threading.Thread(target=self._receive_loop, daemon=True)
        self.receiver_thread.start()

    def _handle_control_message(self, message: dict) -> None:
        mtype = message.get("type")

        if mtype == "server_notice":
            text = str(message.get("text", "")).strip()
            if text:
                self.incoming.put(f"[SERVER] {text}\n")
            return

        if mtype == "roster":
            peers = message.get("peers", [])
            if isinstance(peers, list):
                with self.peers_lock:
                    self.peers.clear()
                    for peer in peers:
                        if not isinstance(peer, dict):
                            continue
                        username = str(peer.get("username", "")).strip()
                        pubkey = str(peer.get("pubkey", "")).strip()
                        if username and pubkey:
                            self.peers[username] = pubkey
            count = len(self.peers)
            self.incoming.put(f"[SECURE] Connected with {count} peer key(s).\n")
            return

        if mtype == "peer_joined":
            username = str(message.get("username", "")).strip()
            pubkey = str(message.get("pubkey", "")).strip()
            if username and pubkey:
                with self.peers_lock:
                    self.peers[username] = pubkey
                self.incoming.put(f"[SERVER] {username} joined (secure channel ready).\n")
            return

        if mtype == "peer_left":
            username = str(message.get("username", "")).strip()
            if username:
                with self.peers_lock:
                    self.peers.pop(username, None)
                self.incoming.put(f"[SERVER] {username} left the chat.\n")
            return

        if mtype == "encrypted":
            self._handle_encrypted(message)

    def _handle_encrypted(self, message: dict) -> None:
        sender = str(message.get("sender", "")).strip()
        sender_pub_b64 = str(message.get("sender_pubkey", "")).strip()
        nonce_b64 = str(message.get("nonce", "")).strip()
        ciphertext_b64 = str(message.get("ciphertext", "")).strip()

        if not (sender and sender_pub_b64 and nonce_b64 and ciphertext_b64):
            return

        if self.private_key is None:
            return

        try:
            sender_pub = self._decode_public_key(sender_pub_b64)
            with self.peers_lock:
                known = self.peers.get(sender)
                if known is None:
                    self.peers[sender] = sender_pub_b64
                elif known != sender_pub_b64:
                    self.incoming.put(f"[WARN] Key mismatch detected for {sender}. Message dropped.\n")
                    return

            shared_secret = self.private_key.exchange(sender_pub)
            key = _derive_key(shared_secret, self.username, sender)
            plaintext = AESGCM(key).decrypt(
                base64.b64decode(nonce_b64),
                base64.b64decode(ciphertext_b64),
                None,
            )
            text = plaintext.decode("utf-8")
        except Exception:
            self.incoming.put(f"[WARN] Could not decrypt a message from {sender}.\n")
            return

        self.incoming.put(f"[{sender}] {text}\n")

    def _receive_loop(self) -> None:
        while not self.stop_event.is_set() and self.socket is not None:
            try:
                chunk = self.socket.recv(4096)
                if not chunk:
                    self.incoming.put("\n[INFO] Disconnected: server closed the connection.\n")
                    self.stop_event.set()
                    break

                self._buffer += chunk
                while b"\n" in self._buffer:
                    line, _, self._buffer = self._buffer.partition(b"\n")
                    if not line.strip():
                        continue
                    try:
                        payload = json.loads(line.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if isinstance(payload, dict):
                        self._handle_control_message(payload)

            except ConnectionResetError:
                self.incoming.put("\n[INFO] Connection reset by server.\n")
                self.stop_event.set()
                break
            except OSError:
                self.stop_event.set()
                break

    def send(self, text: str) -> None:
        if self.socket is None or self.private_key is None:
            raise OSError("No active socket")

        with self.peers_lock:
            peer_items = list(self.peers.items())

        if not peer_items:
            self.incoming.put("[WARN] No peers connected yet. Message not sent.\n")
            return

        for peer_username, peer_pub_b64 in peer_items:
            try:
                peer_pub = self._decode_public_key(peer_pub_b64)
                shared_secret = self.private_key.exchange(peer_pub)
                key = _derive_key(shared_secret, self.username, peer_username)

                aes = AESGCM(key)
                nonce = os.urandom(12)
                ciphertext = aes.encrypt(nonce, text.encode("utf-8"), None)
                self._send_json(
                    self.socket,
                    {
                        "type": "encrypted",
                        "sender": self.username,
                        "sender_pubkey": self.public_key_b64,
                        "recipient": peer_username,
                        "nonce": base64.b64encode(nonce).decode("utf-8"),
                        "ciphertext": base64.b64encode(ciphertext).decode("utf-8"),
                    },
                )
            except Exception as exc:
                self.incoming.put(f"[WARN] Failed secure send to {peer_username}: {exc}\n")

    def disconnect(self) -> None:
        self.stop_event.set()
        if self.socket is None:
            return
        try:
            self.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.socket.close()
        except OSError:
            pass
        self.socket = None


class ChatGUI:
    """A polished Tkinter chat interface layered on top of ChatConnection."""

    THEMES = {
        "midnight": {
            "bg": "#0F172A",
            "panel": "#111827",
            "text": "#E5E7EB",
            "accent": "#22D3EE",
            "subtle": "#94A3B8",
            "input": "#1F2937",
        },
        "sunset": {
            "bg": "#2A0E25",
            "panel": "#3A1C2F",
            "text": "#FFE4EC",
            "accent": "#F97316",
            "subtle": "#FDBA74",
            "input": "#4A2138",
        },
    }

    def __init__(self, connection: ChatConnection) -> None:
        if tk is None or scrolledtext is None:
            raise RuntimeError("Tkinter is not available on this machine.")

        self.connection = connection
        self.theme_name = "midnight"

        self.root = tk.Tk()
        self.show_timestamps = tk.BooleanVar(master=self.root, value=True)
        self.root.title(f"Neon Chat • {self.connection.username}")
        self.root.geometry("920x620")
        self.root.minsize(760, 500)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.unread_count = 0
        self.original_title = self.root.title()

        self.status_var = tk.StringVar(value="🟡 Connecting...")

        self._build_layout()
        self._apply_theme()

        self.root.bind("<Return>", lambda event: self._send_message())
        self.root.bind("<Control-Return>", lambda event: self._insert_newline())
        self.root.bind("<Control-s>", lambda event: self._save_transcript())

        self.root.after(150, self._poll_incoming)

    def _build_layout(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        header = tk.Frame(self.root)
        header.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))
        header.columnconfigure(1, weight=1)

        tk.Label(
            header,
            text="✨ Neon Chat",
            font=("Segoe UI", 18, "bold"),
        ).grid(row=0, column=0, sticky="w")

        tk.Label(
            header,
            textvariable=self.status_var,
            font=("Segoe UI", 10, "bold"),
        ).grid(row=0, column=1, sticky="e")

        body = tk.Frame(self.root)
        body.grid(row=1, column=0, sticky="nsew", padx=12)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=1)

        self.chat_log = scrolledtext.ScrolledText(
            body,
            wrap=tk.WORD,
            font=("Consolas", 11),
            state=tk.DISABLED,
            padx=10,
            pady=10,
            relief=tk.FLAT,
            bd=0,
        )
        self.chat_log.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        sidebar = tk.Frame(body)
        sidebar.grid(row=0, column=1, sticky="nsew")
        sidebar.columnconfigure(0, weight=1)

        tk.Label(sidebar, text="Quick Actions", font=("Segoe UI", 12, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )

        self.timestamp_btn = tk.Checkbutton(
            sidebar,
            text="Show timestamps",
            variable=self.show_timestamps,
            command=lambda: None,
            anchor="w",
        )
        self.timestamp_btn.grid(row=1, column=0, sticky="ew", pady=2)

        tk.Button(sidebar, text="Switch Theme", command=self._toggle_theme).grid(
            row=2, column=0, sticky="ew", pady=2
        )
        tk.Button(sidebar, text="Save Transcript", command=self._save_transcript).grid(
            row=3, column=0, sticky="ew", pady=2
        )
        tk.Button(sidebar, text="Send 👋", command=lambda: self._send_quick("👋 Hello everyone!")).grid(
            row=4, column=0, sticky="ew", pady=2
        )
        tk.Button(sidebar, text="Send 🚀", command=lambda: self._send_quick("🚀 Let's build something awesome!")).grid(
            row=5, column=0, sticky="ew", pady=2
        )
        tk.Button(sidebar, text="/me is coding", command=lambda: self._send_quick("*is coding* ")).grid(
            row=6, column=0, sticky="ew", pady=2
        )

        tip = (
            "Tips:\n"
            "• Enter = send\n"
            "• Ctrl+Enter = new line\n"
            "• Ctrl+S = save log"
        )
        self.tip_label = tk.Label(sidebar, text=tip, justify=tk.LEFT, font=("Segoe UI", 9))
        self.tip_label.grid(row=7, column=0, sticky="nw", pady=(14, 0))

        composer = tk.Frame(self.root)
        composer.grid(row=2, column=0, sticky="ew", padx=12, pady=(8, 12))
        composer.columnconfigure(0, weight=1)

        self.input_box = tk.Text(
            composer,
            height=4,
            wrap=tk.WORD,
            font=("Segoe UI", 11),
            relief=tk.FLAT,
            bd=0,
            padx=10,
            pady=10,
        )
        self.input_box.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        send_btn = tk.Button(composer, text="Send ✨", width=12, command=self._send_message)
        send_btn.grid(row=0, column=1, sticky="ns")

    def _apply_theme(self) -> None:
        t = self.THEMES[self.theme_name]
        self.root.configure(bg=t["bg"])

        def paint(widget: tk.Widget, bg: str, fg: str | None = None) -> None:
            try:
                widget.configure(bg=bg)
                if fg is not None:
                    widget.configure(fg=fg)
            except tk.TclError:
                return

        for widget in self.root.winfo_children():
            paint(widget, t["bg"], t["text"])
            for child in widget.winfo_children():
                paint(child, t["panel"], t["text"])
                for nested in child.winfo_children():
                    paint(nested, t["panel"], t["text"])

        self.chat_log.configure(
            bg=t["panel"],
            fg=t["text"],
            insertbackground=t["accent"],
            selectbackground=t["accent"],
        )
        self.input_box.configure(
            bg=t["input"],
            fg=t["text"],
            insertbackground=t["accent"],
            selectbackground=t["accent"],
        )
        self.tip_label.configure(fg=t["subtle"], bg=t["panel"])

    def _toggle_theme(self) -> None:
        self.theme_name = "sunset" if self.theme_name == "midnight" else "midnight"
        self._apply_theme()
        self._append_local(f"[UI] Theme changed to {self.theme_name}.")

    def _append_chat(self, text: str, local: bool = False) -> None:
        prefix = ""
        if self.show_timestamps.get():
            prefix = datetime.now().strftime("[%H:%M:%S] ")

        line = f"{prefix}{text}"
        if not line.endswith("\n"):
            line += "\n"

        self.chat_log.configure(state=tk.NORMAL)
        self.chat_log.insert(tk.END, line)
        if local:
            self.chat_log.tag_add("local", "end-2l", "end-1l")
            self.chat_log.tag_config("local", foreground="#34D399")
        self.chat_log.configure(state=tk.DISABLED)
        self.chat_log.see(tk.END)

    def _append_local(self, text: str) -> None:
        self._append_chat(text, local=True)

    def _insert_newline(self) -> str:
        self.input_box.insert(tk.INSERT, "\n")
        return "break"

    def _send_quick(self, message: str) -> None:
        self.input_box.delete("1.0", tk.END)
        self.input_box.insert("1.0", message)
        self._send_message()

    def _send_message(self) -> str:
        text = self.input_box.get("1.0", tk.END).strip()
        if not text:
            return "break"

        if text == "/quit":
            self._on_close()
            return "break"

        try:
            self.connection.send(text)
        except (BrokenPipeError, ConnectionResetError, OSError):
            self._append_local("[ERROR] Connection lost while sending message.")
            self.status_var.set("🔴 Disconnected")
            return "break"

        self._append_local(f"[you] {text}")
        self.input_box.delete("1.0", tk.END)
        return "break"

    def _save_transcript(self) -> str:
        if filedialog is None:
            self._append_local("[WARN] File dialogs unavailable in this environment.")
            return "break"

        default_name = f"neon_chat_{int(time.time())}.txt"
        path = filedialog.asksaveasfilename(
            title="Save chat transcript",
            initialfile=default_name,
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return "break"

        content = self.chat_log.get("1.0", tk.END)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        self._append_local(f"[UI] Transcript saved to {path}")
        return "break"

    def _poll_incoming(self) -> None:
        while True:
            try:
                message = self.connection.incoming.get_nowait()
            except queue.Empty:
                break
            self._append_chat(message)
            if not self.root.focus_get():
                self.unread_count += 1
                self.root.title(f"({self.unread_count}) {self.original_title}")

        if self.connection.stop_event.is_set():
            self.status_var.set("🔴 Disconnected")

        if self.root.focus_get() and self.unread_count:
            self.unread_count = 0
            self.root.title(self.original_title)

        self.root.after(150, self._poll_incoming)

    def _on_close(self) -> None:
        self.connection.disconnect()
        self.root.destroy()

    def run(self) -> None:
        self.status_var.set(f"🟢 Connected to {self.connection.host}:{self.connection.port}")
        self._append_local("[INFO] Connected! Enter /quit to leave.")
        self.root.mainloop()


def run_terminal_chat(connection: ChatConnection) -> None:
    print("[INFO] Connected! Type messages and press Enter to send.")
    print("[INFO] Type /quit to leave the chat.")

    while not connection.stop_event.is_set():
        while not connection.incoming.empty():
            print(connection.incoming.get(), end="")

        try:
            message = input()
        except EOFError:
            message = "/quit"
        except KeyboardInterrupt:
            message = "/quit"
            print()

        if message.strip() == "/quit":
            connection.disconnect()
            print("[INFO] You disconnected.")
            break

        if not message.strip():
            continue

        try:
            connection.send(message)
        except (BrokenPipeError, ConnectionResetError):
            print("[ERROR] Connection lost while sending message.")
            connection.stop_event.set()
            break
        except OSError as exc:
            print(f"[ERROR] Send failed: {exc}")
            connection.stop_event.set()
            break


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TCP chat client with optional GUI")
    parser.add_argument("--host", default="127.0.0.1", help="Server host/IP (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5000, help="Server port (default: 5000)")
    parser.add_argument(
        "--ui",
        choices=["auto", "gui", "cli"],
        default="auto",
        help="Interface mode: auto, gui, or cli (default: auto)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    username = input("Choose a username: ").strip()
    if not username:
        username = "anonymous"

    connection = ChatConnection(args.host, args.port, username)

    try:
        connection.connect()
    except ConnectionRefusedError:
        print(f"[ERROR] Connection refused. Is the server running on {args.host}:{args.port}?")
        return
    except OSError as exc:
        print(f"[ERROR] Could not connect: {exc}")
        return

    wants_gui = args.ui == "gui" or (args.ui == "auto" and tk is not None)
    if wants_gui and tk is not None:
        try:
            app = ChatGUI(connection)
            app.run()
            return
        except RuntimeError as exc:
            print(f"[WARN] GUI unavailable ({exc}). Falling back to terminal mode.")

    run_terminal_chat(connection)


if __name__ == "__main__":
    main()
