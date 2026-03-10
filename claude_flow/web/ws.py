"""WebSocket handler for mini task PTY terminals.

Uses flask-sock to bridge browser xterm.js with server-side PTY sessions.
Each WebSocket connection maps to one PTY session via task_id.
"""
from __future__ import annotations

import json
import logging
import os
import select
import threading

logger = logging.getLogger(__name__)


def register_ws_routes(sock, app):
    """Register WebSocket routes on the flask-sock instance."""

    @sock.route("/ws/terminal/<task_id>")
    def terminal(ws, task_id: str):
        """Bidirectional WebSocket bridge to a PTY session.

        Protocol:
        - Client -> Server: raw terminal input (UTF-8 text)
        - Server -> Client: raw terminal output (UTF-8 text)
        - Client -> Server: JSON control: {"type": "resize", "rows": N, "cols": N}
        - Server -> Client: JSON status:  {"type": "status", "alive": bool}
        """
        pty_mgr = app.config.get("PTY_MANAGER")
        if not pty_mgr:
            ws.send(json.dumps({"type": "error", "message": "PTY manager not available"}))
            return

        session = pty_mgr.get_session(task_id)
        if not session:
            ws.send(json.dumps({"type": "error", "message": f"No PTY session for {task_id}"}))
            return

        if not session.alive:
            ws.send(json.dumps({"type": "status", "alive": False}))
            return

        # Send initial prompt if set
        if session.prompt:
            prompt_to_send = session.prompt
            session.prompt = ""
            import time
            time.sleep(1)
            pty_mgr.write(task_id, (prompt_to_send + "\n").encode())

        # Background reader: PTY stdout -> WebSocket
        stop_event = threading.Event()

        def _read_pty():
            while not stop_event.is_set():
                try:
                    readable, _, _ = select.select([session.fd], [], [], 0.1)
                    if readable:
                        data = os.read(session.fd, 4096)
                        if data:
                            try:
                                ws.send(data.decode("utf-8", errors="replace"))
                            except Exception:
                                break
                        else:
                            session.alive = False
                            try:
                                ws.send(json.dumps({"type": "status", "alive": False}))
                            except Exception:
                                pass
                            break
                except OSError:
                    session.alive = False
                    break

        reader = threading.Thread(target=_read_pty, daemon=True)
        reader.start()

        # Main loop: WebSocket input -> PTY stdin
        try:
            while True:
                message = ws.receive()
                if message is None:
                    break

                # Try JSON control message
                try:
                    ctrl = json.loads(message)
                    if isinstance(ctrl, dict) and ctrl.get("type") == "resize":
                        pty_mgr.resize(task_id, ctrl.get("rows", 24), ctrl.get("cols", 80))
                        continue
                except (json.JSONDecodeError, ValueError):
                    pass

                # Raw terminal input
                if isinstance(message, str):
                    pty_mgr.write(task_id, message.encode())
                elif isinstance(message, bytes):
                    pty_mgr.write(task_id, message)
        finally:
            stop_event.set()
            reader.join(timeout=2)
