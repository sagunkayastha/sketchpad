# sketchpad

Draw in any browser, iPad with Apple Pencil or a PC, and send the sketch straight into a running
[Claude Code](https://docs.anthropic.com/en/docs/claude-code) session.

Explaining a layout, a data flow, or "the bump on this plot" is faster with a pen than
with words. sketchpad shows your running Claude Code sessions on the left and an
[Excalidraw](https://github.com/excalidraw/excalidraw) board on the right. **Send**
exports the board to a PNG on the machine that owns the session and types
`<your message> [sketch: /path/to/sketch.png]` into that session, so Claude reads the
image on its next turn.

![Sketch a flow and send it to a session](docs/screenshots/board.png)

## Features

- **Excalidraw board**: pen with Apple Pencil pressure, shapes, arrows, text, eraser,
  undo/redo, zoom and pan. No build step: Excalidraw and React load from esm.sh.
- **Session list**: every running Claude Code session on your machines, with its
  working directory and busy/idle state. Tap one to target it; **Send** names the destination.
- **Recent and History**: quick picks for recently used sessions. History keeps the last 20
  sends on this device, with thumbnails; tap one to put its text back and its sketch on the board
  (as a flat image to annotate further).
- **Open a plot from the machine**: the **Image…** button browses folders on the
  session's machine, drops a PNG/JPEG/SVG onto the board (scaled to fit; drag the corners
  to resize, double-click to crop), and you annotate on top of it. Large raster sources
  shrink on insert; sketches export at up to 2× resolution. Handy for "fix this part of
  the figure".
- **Screenshot the computer you're at** (Windows, Mac or Linux): **Screen ▾** → *This
  computer* opens the browser's share picker; pick a screen or window and one frame lands
  on the board (double-click it to crop). Needs the https:// address; on plain http the
  browser hides the picker, so paste a screenshot instead.
- **Screenshot a machine's desktop**: **Screen ▾** → *Full screen* or *Select box*.
  The machine's desktop screenshot portal handles the capture and area picker. Esc
  cancels. The shot lands on the board like an opened image. A desktop may ask once
  for permission to allow a non-interactive full-screen capture.
- **Screenshot a URL**: **URL…** captures a web page with a headless browser on the
  selected session's machine. `http://localhost:5173` reaches that machine's dev
  server. The shot lands on the board unlocked, ready to resize and annotate.
- **Paste a screenshot from any OS**: take it with the system tool (Windows **Win+Shift+S**,
  macOS **Cmd+Ctrl+Shift+4**, GNOME **PrtSc**), click the board and press **Ctrl+V**. No
  flameshot or helper needed, and it works over plain HTTP. Excalidraw keeps pasted
  images at most 1440 px wide.
- **iPad layout**: touch-sized session rows and buttons; light or dark chrome follows
  the device theme while the drawing board stays white.
- **Multiple machines**: one *hub* serves the page; *helpers* on other machines list and
  deliver to their own sessions. Sketches are saved where the session runs.
- **Login**: username/password with scrypt hashing and a signed 30-day cookie, since the
  page can type into your terminals.
- **Python standard library only** on the server side.

| Annotate a plot Claude just made | Pick an image on the session's machine |
| --- | --- |
| ![Annotate](docs/screenshots/annotate.png) | ![Browser](docs/screenshots/browser.png) |

## How delivery works

Claude Code writes `~/.claude/sessions/<pid>.json` for each running session. sketchpad
reads those, checks the process is still the one that wrote the file (its start time
matches `procStart`, so a reused pid doesn't count), and picks one of two ways in:

1. **Typed into the terminal** when the session runs in a **tmux** pane
   (`tmux send-keys`) or a **kitty** window (`kitty @ send-text`). The message arrives
   exactly as if you had typed it.
2. **Claude Code's own inbox socket** otherwise. Every session listens on a Unix socket
   (`messagingSocketPath` in its session file) with a per-session key next to it. This
   works in any terminal: VS Code, Alacritty, WezTerm, GNOME Terminal, a plain ssh
   session. Claude Code shows the message as coming from another session ("Another
   Claude session sent a message"), so the model treats it as a teammate's request
   rather than as your own typed prompt. For sketches and questions that makes no
   practical difference.

The socket is an internal Claude Code interface (present in 2.1.x). If a future
version changes it, tmux and kitty keep working.
The socket does not acknowledge delivery, so “Sent to X via inbox” means the message
was handed to the socket. If tmux or kitty types a message but cannot press Enter,
sketchpad reports that it was typed but not submitted and asks you to press Enter
in that terminal.

## Requirements

- Python 3.9+ (no packages needed to run)
- A reachable desktop session bus and system libgio on machines whose screen **Screen**
  should capture. The desktop portal supplies the area picker; no flameshot is used.
- Google Chrome, Chromium, or `chromium-browser` on a machine whose URLs **URL…**
  should capture. The server uses its headless CLI; no Python browser package is needed.
- Claude Code 2.1 or newer, in any terminal. Sessions inside **tmux** or **kitty** get
  the message typed in; everything else goes through the session's inbox socket.
- For kitty: add to `kitty.conf` and restart kitty

  ```
  allow_remote_control socket-only
  listen_on unix:${XDG_RUNTIME_DIR}/kitty-{kitty_pid}
  ```

- A browser on the same network. Safari on iPad with an Apple Pencil is the intended
  client, but any desktop browser works.

## Quick start (one machine)

```sh
git clone https://github.com/sagunkayastha/sketchpad
cd sketchpad
python3 server.py set-password                # creates ~/.config/sketchpad/auth.json
python3 server.py serve --bind 192.168.1.10   # your LAN address; port 8790
```

Open `http://192.168.1.10:8790` on the iPad, log in, pick a session, draw, Send.

Bind only interfaces you trust. Anyone who can log in can type into your terminals, so
don't bind `0.0.0.0` on a network you don't control. The default bind is `127.0.0.1`.

Sketches are written to `sketches/` next to `server.py` (gitignored). Nothing cleans
them up.

## Two or more machines

The hub machine serves the page and its own sessions. Each other machine runs a
*helper* (API only, same code) that the hub calls. Both sides share a token.

```sh
# on every machine, same content
mkdir -p ~/.config/sketchpad && openssl rand -hex 32 > ~/.config/sketchpad/helper-token

# helper machine (e.g. a laptop)
python3 server.py helper --bind 127.0.0.1          # port 8791

# hub machine
python3 server.py serve --bind 192.168.1.10 --remote laptop=http://127.0.0.1:8791
```

The hub reaches the helper at the URL you give. If the helper is a laptop the hub
can't reach directly, run a reverse SSH tunnel from the laptop instead:
`deploy/tunnel.sh` keeps `hub:127.0.0.1:8791` pointed at the laptop's helper
(set `SKETCHPAD_HUB` to the hub's ssh host alias).

`deploy/` has systemd user units for the hub, the helper and the tunnel, and an
optional Tailscale sidecar (`docker-compose.yml`) that exposes the hub over HTTPS on
your tailnet for use away from home. Edit the addresses in them before installing.

## Command reference

```
python3 server.py set-password              set the login (asks interactively)
python3 server.py serve  [--bind ADDR]... [--port 8790] [--remote NAME=URL]...
python3 server.py helper [--bind ADDR]... [--port 8791]
```

`--bind` and `--remote` can be repeated. A helper needs `~/.config/sketchpad/helper-token`;
the hub needs it only when `--remote` is used.

## Development

```sh
PYTHONPATH=. python3 -m unittest discover -s tests   # unit tests, stdlib only
pip install playwright && playwright install chrome
python3 tests/e2e_ui.py                              # browser test against an isolated hub
```

The browser test creates a fake Claude session behind a Unix socket and isolates
`XDG_RUNTIME_DIR` and `TMUX_TMPDIR`, so it never types into real terminals.

Layout:

| File | Purpose |
| --- | --- |
| `server.py` | HTTP server: hub and helper roles, routes, CLI |
| `sessions.py` | find Claude Code sessions, pick tmux pane / kitty window / inbox socket, deliver text |
| `files.py` | folder listing and image reading for the **Image…** browser |
| `screen.py` | desktop portal screenshots for the **Screen** button |
| `urlshot.py` | headless browser capture for **URL…** on the selected machine |
| `auth.py` | password hashing and signed cookies |
| `static/` | the page: `index.html`, `app.js` (Excalidraw mount, session list, send), `login.html`, `style.css` |
| `deploy/` | systemd units, tunnel script, Tailscale sidecar |

## Limitations

- Outside tmux and kitty the message is framed as a peer message, not user input (see above).
- The list checks tmux/kitty routes at most every 30 s, so a just-started session can show the
  wrong route (or "not reachable") briefly. Sending always checks fresh.
- The Excalidraw eraser deletes whole elements, not parts of a stroke (upstream behaviour).
- SVG sources keep their original data; very complex SVGs can still make a tab sluggish.
- Sketches accumulate on disk. Deleting one leaves its History row, but that sketch
  can no longer be reopened.

## License

MIT, see [LICENSE](LICENSE).
