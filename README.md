# AS_Test_Project
A test repo for a project of mine.

## What's inside
- `server.py`: threaded TCP chat server
- `client.py`: chat client with a modern Tkinter UI and terminal fallback

## Run
### 1) Start the server
```bash
python3 server.py --host 127.0.0.1 --port 5000
```

### 2) Start the client
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
