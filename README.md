# AS_Test_Project
A test repo for a project of mine.

## What's inside
- `server.py`: threaded TCP chat server that routes encrypted packets
- `client.py`: chat client with a modern Tkinter UI and terminal fallback

## Security upgrade: End-to-End Encryption (E2EE)
The chat now uses **Diffie-Hellman key exchange (X25519)** and **AES-GCM** encryption:
- Each client generates its own X25519 keypair at connect time.
- Public keys are exchanged through the server.
- Every outgoing message is encrypted client-to-client before being sent.
- The server only relays ciphertext and cannot decrypt chat content.

## Requirements
Install dependencies (Python 3.10+ recommended):

```bash
pip install cryptography
```

## Run
### 1) Start the server
```bash
python3 server.py --host 127.0.0.1 --port 5000
```

### 2) Start clients (at least two)
```bash
python3 client.py --host 127.0.0.1 --port 5000 --ui auto
```

## Client UI modes
- `--ui auto`: uses GUI if available, otherwise terminal mode
- `--ui gui`: force desktop GUI
- `--ui cli`: force terminal mode

## Cool client features
- Neon-styled desktop UI with two themes (Midnight/Sunset)
- Quick-action buttons for instant messages
- Optional timestamps
- Unread message counter in the window title
- Save chat transcript to text file (`Ctrl+S`)
- Enter to send, `Ctrl+Enter` for newline

## Cool server features
- `/who` returns a live list of connected users
- `/dm <username> <message>` sends a private message (DM) to one user
