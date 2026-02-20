#!/usr/bin/env python3
"""
MvC2 Skin Gallery — Fast triage tool.

Shows skins one at a time. Press Y to keep, N to skip.
Left/Right arrows to navigate and change previous decisions.
Decisions saved to verdicts.tsv after each keypress.
On reload, jumps to first unreviewed skin.

Usage:
    python gallery.py <merged_folder>
    python gallery.py <merged_folder> --port 8080
"""
import argparse
import http.server
import json
import os
import sys
import threading
import urllib.parse
import webbrowser


def scan_skins(root_dir):
    """Scan merged folder, return {character: [filenames]} sorted."""
    characters = {}
    for char_name in sorted(os.listdir(root_dir)):
        char_dir = os.path.join(root_dir, char_name)
        if not os.path.isdir(char_dir):
            continue
        pngs = sorted(f for f in os.listdir(char_dir) if f.lower().endswith('.png'))
        if pngs:
            characters[char_name] = pngs
    return characters


def load_verdicts(verdicts_file):
    """Load existing verdicts from file. Last entry per key wins."""
    verdicts = {}
    if os.path.isfile(verdicts_file):
        with open(verdicts_file, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split('\t')
                if len(parts) == 2:
                    verdicts[parts[0]] = parts[1]
    return verdicts


def build_gallery_html(all_skins, verdicts):
    """Build the single-page gallery HTML with all skins and existing verdicts."""

    # Find first unreviewed index
    first_unreviewed = 0
    for i, s in enumerate(all_skins):
        if s["key"] not in verdicts:
            first_unreviewed = i
            break
    else:
        first_unreviewed = 0  # all reviewed, start at beginning

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>MvC2 Skin Gallery</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    background: #111; color: #eee; font-family: 'Segoe UI', system-ui, sans-serif;
    display: flex; flex-direction: column; height: 100vh; overflow: hidden;
}}
#topbar {{
    background: #1a1a2e; padding: 8px 16px; display: flex; align-items: center;
    gap: 16px; border-bottom: 2px solid #333; flex-shrink: 0;
}}
#topbar h1 {{ font-size: 18px; color: #e94560; }}
#stats {{ font-size: 13px; color: #888; margin-left: auto; }}
#stats .g {{ color: #4eff4e; font-weight: bold; }}
#stats .r {{ color: #ff4e4e; font-weight: bold; }}
#stats .w {{ color: #fff; font-weight: bold; }}

#main {{
    display: flex; flex: 1; overflow: hidden;
}}
#sidebar {{
    width: 220px; background: #1a1a1a; border-right: 1px solid #333;
    overflow-y: auto; flex-shrink: 0; padding: 8px 0;
}}
.char-btn {{
    display: block; width: 100%; padding: 6px 12px; background: none;
    border: none; color: #ccc; text-align: left; font-size: 13px;
    cursor: pointer; border-left: 3px solid transparent;
}}
.char-btn:hover {{ background: #252525; }}
.char-btn.active {{ background: #1a1a2e; color: #e94560; border-left-color: #e94560; }}
.char-btn.done {{ color: #666; }}
.char-info {{ float: right; font-size: 11px; }}
.char-info .g {{ color: #4eff4e; }}
.char-info .r {{ color: #ff4e4e; }}
.char-info .rem {{ color: #888; }}

#viewer {{
    flex: 1; display: flex; flex-direction: column; align-items: center;
    justify-content: center; padding: 20px; position: relative;
}}
#skin-img {{
    max-width: 95%; max-height: calc(100vh - 180px);
    image-rendering: pixelated; background: #222;
    border: 3px solid #333; border-radius: 4px;
}}
#skin-img.kept {{ border-color: #4eff4e; }}
#skin-img.skipped {{ border-color: #ff4e4e; }}
#info {{
    margin-top: 12px; text-align: center;
}}
#filename {{ font-size: 16px; font-weight: bold; color: #ddd; }}
#position {{ color: #888; margin-top: 4px; font-size: 13px; }}
#verdict-label {{ margin-top: 4px; font-size: 14px; font-weight: bold; }}
#verdict-label.kept {{ color: #4eff4e; }}
#verdict-label.skipped {{ color: #ff4e4e; }}
#verdict-label.pending {{ color: #555; }}

#controls {{
    background: #1a1a2e; padding: 12px 16px; display: flex;
    justify-content: center; gap: 16px; border-top: 2px solid #333;
    flex-shrink: 0;
}}
.ctrl-btn {{
    padding: 10px 28px; border: 2px solid #444; border-radius: 6px;
    background: #222; color: #ccc; font-size: 15px; cursor: pointer;
    font-weight: bold;
}}
.ctrl-btn:hover {{ background: #333; }}
.ctrl-btn.keep {{ border-color: #4eff4e; color: #4eff4e; }}
.ctrl-btn.keep:hover {{ background: #1a3a1a; }}
.ctrl-btn.skip {{ border-color: #ff4e4e; color: #ff4e4e; }}
.ctrl-btn.skip:hover {{ background: #3a1a1a; }}
.ctrl-btn.nav {{ border-color: #555; color: #aaa; }}
.ctrl-btn.nav:hover {{ background: #2a2a2a; }}
kbd {{
    background: #333; padding: 2px 6px; border-radius: 3px; font-size: 11px;
    border: 1px solid #555; margin-left: 6px;
}}
</style>
</head>
<body>

<div id="topbar">
    <h1>MvC2 Skin Gallery</h1>
    <div id="stats">
        Kept: <span class="g" id="kept-count">0</span> |
        Skipped: <span class="r" id="skipped-count">0</span> |
        Remaining: <span class="w" id="remaining-count">0</span> |
        Total: <span id="total-count">0</span>
    </div>
</div>

<div id="main">
    <div id="sidebar"></div>
    <div id="viewer">
        <img id="skin-img" src="" alt="skin">
        <div id="info">
            <div id="filename"></div>
            <div id="position"></div>
            <div id="verdict-label" class="pending"></div>
        </div>
    </div>
</div>

<div id="controls">
    <button class="ctrl-btn skip" onclick="doSkip()">Skip <kbd>N</kbd></button>
    <button class="ctrl-btn nav" onclick="goPrev()">Prev <kbd>&larr;</kbd></button>
    <button class="ctrl-btn nav" onclick="goNext()">Next <kbd>&rarr;</kbd></button>
    <button class="ctrl-btn keep" onclick="doKeep()">Keep <kbd>Y</kbd></button>
</div>

<script>
const ALL = {json.dumps(all_skins)};
const TOTAL = ALL.length;
const verdicts = {json.dumps(verdicts)};
let idx = {first_unreviewed};

function getCharStats() {{
    const stats = {{}};
    ALL.forEach(d => {{
        if (!stats[d.char]) stats[d.char] = {{ total: 0, kept: 0, skipped: 0, remaining: 0 }};
        stats[d.char].total++;
        const v = verdicts[d.key];
        if (v === 'keep') stats[d.char].kept++;
        else if (v === 'skip') stats[d.char].skipped++;
        else stats[d.char].remaining++;
    }});
    return stats;
}}

function updateStats() {{
    const vals = Object.values(verdicts);
    const kept = vals.filter(v => v === 'keep').length;
    const skipped = vals.filter(v => v === 'skip').length;
    document.getElementById('kept-count').textContent = kept;
    document.getElementById('skipped-count').textContent = skipped;
    document.getElementById('remaining-count').textContent = TOTAL - kept - skipped;
    document.getElementById('total-count').textContent = TOTAL;
}}

function buildSidebar() {{
    const sb = document.getElementById('sidebar');
    sb.innerHTML = '';
    const stats = getCharStats();
    const curChar = idx < ALL.length ? ALL[idx].char : '';
    Object.keys(stats).sort().forEach(c => {{
        const s = stats[c];
        const btn = document.createElement('button');
        const isDone = s.remaining === 0;
        btn.className = 'char-btn' + (c === curChar ? ' active' : '') + (isDone ? ' done' : '');
        btn.onclick = () => jumpToChar(c);
        let info = '';
        if (s.kept > 0) info += '<span class="g">' + s.kept + '</span> ';
        if (s.skipped > 0) info += '<span class="r">' + s.skipped + '</span> ';
        if (s.remaining > 0) info += '<span class="rem">' + s.remaining + '</span>';
        else info += 'done';
        btn.innerHTML = c.replace(/_/g, ' ') + '<span class="char-info">' + info + '</span>';
        sb.appendChild(btn);
    }});
}}

function jumpToChar(charName) {{
    // Jump to first unreviewed in that character, or first skin if all reviewed
    let firstUnreviewed = -1, firstInChar = -1;
    for (let i = 0; i < ALL.length; i++) {{
        if (ALL[i].char === charName) {{
            if (firstInChar === -1) firstInChar = i;
            if (firstUnreviewed === -1 && !verdicts[ALL[i].key]) firstUnreviewed = i;
        }}
    }}
    idx = firstUnreviewed >= 0 ? firstUnreviewed : firstInChar;
    showCurrent();
}}

function showCurrent() {{
    if (idx < 0) idx = 0;
    if (idx >= ALL.length) idx = ALL.length - 1;
    const d = ALL[idx];
    const img = document.getElementById('skin-img');
    img.src = d.path;
    const v = verdicts[d.key];
    img.className = v === 'keep' ? 'kept' : v === 'skip' ? 'skipped' : '';

    document.getElementById('filename').textContent = d.file;
    document.getElementById('position').textContent =
        (idx + 1) + ' / ' + TOTAL + '  |  ' + d.char.replace(/_/g, ' ');

    const vl = document.getElementById('verdict-label');
    if (v === 'keep') {{ vl.textContent = 'KEPT'; vl.className = 'kept'; }}
    else if (v === 'skip') {{ vl.textContent = 'SKIPPED'; vl.className = 'skipped'; }}
    else {{ vl.textContent = 'unreviewed'; vl.className = 'pending'; }}

    buildSidebar();
    updateStats();
}}

function setVerdict(verdict) {{
    if (idx >= ALL.length) return;
    const d = ALL[idx];
    verdicts[d.key] = verdict;
    fetch('/verdict', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ key: d.key, verdict: verdict }})
    }});
    showCurrent();
    // Auto-advance to next unreviewed
    advanceToNext();
}}

function advanceToNext() {{
    // Find next unreviewed from current position
    for (let i = idx + 1; i < ALL.length; i++) {{
        if (!verdicts[ALL[i].key]) {{ idx = i; showCurrent(); return; }}
    }}
    // Wrap around from beginning
    for (let i = 0; i < idx; i++) {{
        if (!verdicts[ALL[i].key]) {{ idx = i; showCurrent(); return; }}
    }}
    // All reviewed — stay put
    showCurrent();
}}

function doKeep() {{ setVerdict('keep'); }}
function doSkip() {{ setVerdict('skip'); }}
function goNext() {{ if (idx < ALL.length - 1) {{ idx++; showCurrent(); }} }}
function goPrev() {{ if (idx > 0) {{ idx--; showCurrent(); }} }}

document.addEventListener('keydown', e => {{
    switch(e.key) {{
        case 'y': case '1': doKeep(); break;
        case 'n': case '2': doSkip(); break;
        case 'ArrowRight': case 'd': goNext(); break;
        case 'ArrowLeft': case 'a': goPrev(); break;
    }}
}});

showCurrent();
</script>
</body>
</html>"""


class GalleryHandler(http.server.BaseHTTPRequestHandler):
    def __init__(self, *args, root_dir="", verdicts_file="", html="", **kwargs):
        self.root_dir = root_dir
        self.verdicts_file = verdicts_file
        self.html = html
        super().__init__(*args, **kwargs)

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(self.html.encode('utf-8'))
        elif self.path.startswith("/img/"):
            rel = urllib.parse.unquote(self.path[5:])
            filepath = os.path.join(self.root_dir, rel)
            if os.path.isfile(filepath):
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "max-age=3600")
                self.end_headers()
                with open(filepath, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self.send_error(404)
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/verdict":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            key = body["key"]
            verdict = body["verdict"]
            # Append to verdicts file (last entry per key wins on reload)
            with open(self.verdicts_file, 'a') as f:
                f.write(f"{key}\t{verdict}\n")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        else:
            self.send_error(404)

    def log_message(self, format, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description="MvC2 Skin Gallery — fast triage")
    parser.add_argument("input", help="Merged skins folder to review")
    parser.add_argument("--port", type=int, default=8420, help="Server port (default: 8420)")
    parser.add_argument("--verdicts", help="Verdicts file path (default: <input>/verdicts.tsv)")
    args = parser.parse_args()

    root_dir = os.path.abspath(args.input)
    verdicts_file = args.verdicts or os.path.join(root_dir, "verdicts.tsv")

    characters = scan_skins(root_dir)
    total = sum(len(v) for v in characters.values())
    verdicts = load_verdicts(verdicts_file)
    reviewed = len(verdicts)
    remaining = total - reviewed

    print("=" * 60)
    print("MvC2 Skin Gallery")
    print("=" * 60)
    print(f"Source:   {root_dir}")
    print(f"Verdicts: {verdicts_file}")
    print(f"Total:    {total} skins across {len(characters)} characters")
    print(f"Reviewed: {reviewed} ({sum(1 for v in verdicts.values() if v == 'keep')} kept, "
          f"{sum(1 for v in verdicts.values() if v == 'skip')} skipped)")
    print(f"Queue:    {remaining} unreviewed")
    print()
    print("Controls:  Y = Keep  |  N = Skip  |  Left/Right = Navigate")
    print()

    # Build all skins list
    all_skins = []
    for char_name, files in characters.items():
        for fname in files:
            key = f"{char_name}/{fname}"
            all_skins.append({
                "char": char_name,
                "file": fname,
                "key": key,
                "path": f"/img/{char_name}/{urllib.parse.quote(fname)}",
            })

    html = build_gallery_html(all_skins, verdicts)

    def handler_factory(*a, **kw):
        return GalleryHandler(*a, root_dir=root_dir, verdicts_file=verdicts_file, html=html, **kw)

    server = http.server.HTTPServer(("127.0.0.1", args.port), handler_factory)
    url = f"http://127.0.0.1:{args.port}"

    print(f"Gallery running at {url}")
    print("Press Ctrl+C to stop\n")

    threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
