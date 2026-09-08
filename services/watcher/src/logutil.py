"""Logging helpers shared with the watcher API."""

from __future__ import annotations

import html
import logging
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path


class MemoryLogHandler(logging.Handler):
    def __init__(self, maxlen: int = 500):
        super().__init__()
        self.buffer: deque[str] = deque(maxlen=maxlen)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.buffer.append(self.format(record))
        except Exception:
            self.handleError(record)

    def lines(self, n: int = 50) -> list[str]:
        return list(self.buffer)[-n:]


memory_handler = MemoryLogHandler()


def parse_console_params(query) -> tuple[int, int, bool]:
    try:
        lines = min(max(int(query.get("lines", "200")), 1), 500)
    except (TypeError, ValueError):
        lines = 200
    try:
        refresh = max(int(query.get("refresh", "5")), 1)
    except (TypeError, ValueError):
        refresh = 5
    autoscroll = str(query.get("autoscroll", "false")).lower() == "true"
    return lines, refresh, autoscroll


def render_console_html(
    title: str,
    lines: list[str],
    refresh: int = 5,
    autoscroll: bool = False,
) -> str:
    processed: list[str] = []
    for line in lines:
        escaped = html.escape(line)
        if "[WARNING]" in line or "[WARN]" in line:
            processed.append(f'<span style="color: #f9c513;">{escaped}</span>')
        elif "[ERROR]" in line:
            processed.append(f'<span style="color: #f85149;">{escaped}</span>')
        else:
            processed.append(escaped)
    processed.append("")
    logs_html = "\n".join(processed)
    autoscroll_js = "true" if autoscroll else "false"
    autoscroll_class = "active" if autoscroll else ""
    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{html.escape(title)}</title>
    <style>
        body {{
            margin: 0;
            padding: 0;
            background-color: #0d1117;
            color: #c9d1d9;
            font-family: 'Courier New', Consolas, monospace;
            font-size: 11px;
            overflow: hidden;
        }}
        #header {{
            background: #161b22;
            padding: 8px 15px;
            border-bottom: 1px solid #30363d;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        #title {{
            font-weight: bold;
            color: #ffffff;
            font-size: 13px;
        }}
        #controls {{
            display: flex;
            gap: 8px;
        }}
        button {{
            background: #238636;
            color: white;
            border: none;
            padding: 5px 10px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 11px;
            font-weight: 500;
        }}
        button:hover {{
            background: #2ea043;
        }}
        #refresh-btn {{
            background: #1f6feb;
        }}
        #refresh-btn:hover {{
            background: #388bfd;
        }}
        #autoscroll-btn {{
            background: #6e7681;
        }}
        #autoscroll-btn.active {{
            background: #238636;
        }}
        #logs {{
            padding: 15px;
            height: calc(100vh - 45px);
            overflow-y: auto;
            line-height: 1.4;
            white-space: pre-wrap;
            word-wrap: break-word;
            color: #c9d1d9;
        }}
    </style>
</head>
<body>
    <div id="header">
        <div id="title">{html.escape(title)}</div>
        <div id="controls">
            <button id="autoscroll-btn" class="{autoscroll_class}">
                Auto-scroll
            </button>
            <button id="refresh-btn">Refresh</button>
        </div>
    </div>
    <div id="logs">{logs_html}</div>
    <script>
        let autoScroll = sessionStorage.getItem('autoScroll') !== null
            ? sessionStorage.getItem('autoScroll') === 'true'
            : {autoscroll_js};
        if (autoScroll) {{
            document.getElementById('autoscroll-btn').classList.add('active');
        }}
        window.addEventListener('beforeunload', function() {{
            sessionStorage.setItem('autoScroll', autoScroll);
            if (!autoScroll) {{
                sessionStorage.setItem('scrollPos', document.getElementById('logs').scrollTop);
            }}
        }});
        window.addEventListener('load', function() {{
            if (!autoScroll) {{
                const savedPos = sessionStorage.getItem('scrollPos');
                if (savedPos !== null) {{
                    document.getElementById('logs').scrollTop = parseInt(savedPos);
                }}
            }} else {{
                scrollToBottom();
            }}
        }});
        function scrollToBottom() {{
            const logsDiv = document.getElementById('logs');
            logsDiv.scrollTop = logsDiv.scrollHeight;
        }}
        document.getElementById('autoscroll-btn').addEventListener('click', function() {{
            autoScroll = !autoScroll;
            this.classList.toggle('active');
            sessionStorage.setItem('autoScroll', autoScroll);
            if (autoScroll) {{
                scrollToBottom();
                sessionStorage.removeItem('scrollPos');
            }}
        }});
        document.getElementById('refresh-btn').addEventListener('click', function() {{
            location.reload();
        }});
        setInterval(function() {{
            location.reload();
        }}, {int(refresh)} * 1000);
        scrollToBottom();
    </script>
</body>
</html>"""


def setup_logger(name: str, log_file: Path, level: str = "INFO") -> logging.Logger:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    memory_handler.setFormatter(formatter)

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level, logging.INFO))
    logger.handlers.clear()
    logger.addHandler(file_handler)
    logger.addHandler(console)
    logger.addHandler(memory_handler)
    logger.propagate = False
    return logger
