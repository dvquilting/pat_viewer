"""
Statler Stitcher QLI support: convert a .qli file to a PatFile in memory
(faithful Python port of the reference qli2pat.c logic), and write a
PatFile back out as QLI (used by SVG → QLI conversion).
"""

import math

from models import Move, PatFile

def _qli_get_number(src: str) -> tuple[str, int]:
    """
    Extract the next comma-delimited number token from *src*.
    Returns (token_string, characters_consumed).
    Strips leading whitespace, @RND[ prefix, trailing ] and CR/LF/+/* chars.
    """
    i = 0
    n = len(src)
    while i < n and src[i] == ' ':
        i += 1
    numstart = i
    while i < n and src[i] != ',':
        i += 1
    token = src[numstart:i]
    # Strip @RND[ prefix (always 5 chars: @RND[)
    if token.startswith('@RND['):
        token = token[5:]
    # Strip trailing ] and control/special chars
    for ch in (']', '\r', '\n', '*', '+'):
        idx = token.find(ch)
        if idx != -1:
            token = token[:idx]
    return token.strip(), i


def _qli_try_float(s: str) -> float:
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def _qli_skip_axes(uc: str, i: int, n: int) -> int:
    """Advance *i* past an axis specifier (S, Z, XY, X,Y, …) after BG/AM/VM."""
    while i + 1 < n and uc[i + 1] in 'SX,YZ':
        i += 1
    return i


def _qli_skip_cr_suffix(uc: str, i: int, n: int) -> int:
    """Skip optional CR speed / offset suffixes: <speed> and >o."""
    while i < n and uc[i] in ' \t':
        i += 1
    if i < n and uc[i] == '<':
        i += 1
        _, consumed = _qli_get_number(uc[i:])
        i += consumed
        if i < n and uc[i] == '>':
            i += 1
    while i < n and uc[i] in ' \t':
        i += 1
    if i < n and uc[i] == '>':
        i += 1
        _, consumed = _qli_get_number(uc[i:])
        i += consumed
    return i


class QliConverter:
    """
    Converts a Statler Stitcher QLI file to a PatFile in memory.
    Mirrors the logic of qli2pat.c exactly.
    """
    SCALE = 0.001   # QLI units → PAT units

    def __init__(self):
        self.reset()

    def reset(self):
        self.relative_x = 0.0
        self.relative_y = 0.0
        self.current_x  = 0.0
        self.current_y  = 0.0
        self.next_x     = 0.0
        self.next_y     = 0.0
        self.needle     = 1        # 1=down (stitch), 0=up (travel)
        self.pat_line   = 11
        self.comments: list[str] = []
        self.moves: list[Move]   = []

    # ------------------------------------------------------------------
    def _emit_linear(self):
        """Emit a G00/G01 linear move to next_x, next_y."""
        seq   = self.pat_line
        gcode = self.needle          # 0=travel, 1=stitch
        x     = self.SCALE * self.next_x
        y     = self.SCALE * self.next_y
        self.moves.append(Move(seq, gcode, x, y,
                               is_stitch=(gcode == 1)))
        self.pat_line += 1

    def _emit_arc(self, circle_cmd: int, next_x: float, next_y: float,
                  rcx: float, rcy: float):
        """Emit a G02/G03 arc move."""
        seq = self.pat_line
        x   = self.SCALE * next_x
        y   = self.SCALE * next_y
        self.moves.append(Move(seq, circle_cmd, x, y, rcx, rcy,
                               is_stitch=True))
        self.pat_line += 1

    # ------------------------------------------------------------------
    def _parse_cmd(self, cmd: str):
        uc = cmd.upper()
        n  = len(uc)

        i = 0
        while i < n:
            c = uc[i]

            # ---- A: AM or AN ----------------------------------------
            if c == 'A':
                if i+1 < n and uc[i+1] == 'M':
                    i += 1
                    i = _qli_skip_axes(uc, i, n)
                elif i+1 < n and uc[i+1] == 'N':
                    i += 1

            # ---- B: BG ----------------------------------------------
            elif c == 'B':
                if i+1 < n and uc[i+1] == 'G':
                    i += 1
                    i = _qli_skip_axes(uc, i, n)

            # ---- C: CR or CS ----------------------------------------
            elif c == 'C':
                if i+1 < n and uc[i+1] == 'R':
                    # Circle: radius, start_angle, angle_range
                    i += 2
                    tok1, consumed = _qli_get_number(uc[i:])
                    i += consumed + 1
                    tok2, consumed = _qli_get_number(uc[i:])
                    i += consumed + 1
                    tok3, consumed = _qli_get_number(uc[i:])
                    i += consumed
                    i = _qli_skip_cr_suffix(uc, i, n)

                    radius      = _qli_try_float(tok1)
                    start_angle = _qli_try_float(tok2)
                    angle_range = _qli_try_float(tok3)

                    circle_cmd = 2 if angle_range < 0 else 3

                    sa    = math.radians(start_angle)
                    ea    = sa + math.radians(angle_range)
                    sp_x  = radius * math.cos(sa)
                    sp_y  = radius * math.sin(sa)
                    ep_x  = radius * math.cos(ea)
                    ep_y  = radius * math.sin(ea)

                    self.next_x = self.current_x + (ep_x - sp_x)
                    self.next_y = self.current_y + (ep_y - sp_y)

                    if self.needle == 0:
                        # Needle off: nothing is sewn, so this is a travel
                        # move; PAT travel moves are straight.
                        self._emit_linear()
                    else:
                        # Circle centre, in QLI units.
                        cx = self.current_x - sp_x
                        cy = self.current_y - sp_y
                        # A PAT arc whose end meets its start is sewn as a
                        # semicircle, so a full circle must not be written as
                        # one arc. Arcs over 180° are split into equal pieces
                        # of at most 180° (a split 270° arc sews the same).
                        pieces = max(1, math.ceil(abs(angle_range) / 180.0 - 1e-9))
                        step = math.radians(angle_range) / pieces
                        for k in range(1, pieces + 1):
                            if k == pieces:
                                px, py = self.next_x, self.next_y
                            else:
                                a = sa + step * k
                                px = cx + radius * math.cos(a)
                                py = cy + radius * math.sin(a)
                            # I/J = absolute center, scaled to PAT units
                            self._emit_arc(circle_cmd, px, py,
                                           self.SCALE * cx, self.SCALE * cy)
                    self.current_x = self.next_x
                    self.current_y = self.next_y

                elif i+1 < n and uc[i+1] == 'S':
                    # Clear sequence – reset to origin
                    self.current_x = 0.0
                    self.current_y = 0.0

            # ---- E: EN ----------------------------------------------
            elif c == 'E':
                pass   # end marker – nothing to emit

            # ---- N: NO (needle / info) ------------------------------
            elif c == 'N':
                if i+1 < n and uc[i+1] == 'O':
                    rest = cmd[i+2:]   # use original case for comment text
                    uc_rest = uc[i+2:]
                    if 'NEEDLE OFF' in uc_rest:
                        self.needle = 0
                    elif 'NEEDLE ON' in uc_rest:
                        self.needle = 1
                    elif 'INFO' in uc_rest:
                        # "NO INFO <text>" – grab the comment text
                        info_pos = uc_rest.find('INFO')
                        comment  = rest[info_pos + 4:].strip(' \r\n\t')
                        self.comments.append(comment)
                        i = n - 1   # rest of line consumed

            # ---- R: REM ---------------------------------------------
            elif c == 'R':
                if i+2 < n and uc[i+1] == 'E' and uc[i+2] == 'M':
                    comment_text = cmd[i+4:].strip(' \r\n\t')
                    # Skip FIRST_MACRO / END MACRO / CONTINUOUS markers
                    uc_line = uc
                    if ('FIRST_MACRO' not in uc_line and
                            'END MACRO'   not in uc_line and
                            'CONTINUOUS'  not in uc_line):
                        self.comments.append(comment_text)
                    i = n - 1

            # ---- V: VE, VP, VM --------------------------------------
            elif c == 'V':
                if i+1 < n and uc[i+1] == 'E':
                    # Vector End: set relative origin to current position
                    self.relative_x = self.current_x
                    self.relative_y = self.current_y
                    i += 1

                elif i+1 < n and uc[i+1] == 'P':
                    # Vector Position: move relative to relative origin
                    i += 2
                    tok1, consumed = _qli_get_number(uc[i:])
                    i += consumed + 1
                    tok2, consumed = _qli_get_number(uc[i:])
                    i += consumed

                    dx = _qli_try_float(tok1)
                    dy = _qli_try_float(tok2)
                    self.next_x = self.relative_x + dx
                    self.next_y = self.relative_y + dy

                    self._emit_linear()
                    self.current_x = self.next_x
                    self.current_y = self.next_y

                elif i+1 < n and uc[i+1] == 'M':
                    # Vector Motion: VMXY, VMYX, VMS, …
                    i += 1
                    i = _qli_skip_axes(uc, i, n)

            # ---- W: WW (unknown, ignore) ----------------------------
            elif c == 'W':
                if i+1 < n and uc[i+1] == 'W':
                    i += 1

            i += 1

    # ------------------------------------------------------------------
    def _parse_line(self, line: str):
        """Split a QLI line on semicolons and dispatch each command."""
        for part in line.split(';'):
            part = part.strip()
            if part:
                self._parse_cmd(part)

    # ------------------------------------------------------------------
    def convert(self, path: str) -> PatFile:
        """Read a .QLI file and return an equivalent PatFile."""
        self.reset()
        pat = PatFile(filename=path)

        # Header move: N10 G00 X0 Y0
        pat.moves.append(Move(10, 0, 0.0, 0.0, is_stitch=False))

        with open(path, 'r', encoding='utf-8', errors='replace') as fh:
            for raw in fh:
                self._parse_line(raw.strip())

        pat.moves.extend(self.moves)
        pat.comments = self.comments
        pat.end_seq  = self.pat_line
        return pat


# ------------------------------------------------------------------ write QLI


def _fmt_qli_num(value: float) -> str:
    """Format a QLI coordinate / radius compactly."""
    # Prefer integers when the value is effectively whole (common for mils).
    rounded = round(value)
    if abs(value - rounded) < 1e-6:
        return str(int(rounded))
    return f"{value:.4f}".rstrip('0').rstrip('.')


def write_qli_file(pat: PatFile, out_path: str) -> str:
    """
    Write a PatFile to disk as a Statler Stitcher QLI file.

    Inverse of QliConverter: PAT units → QLI units via 1/SCALE (×1000).
    G00/G01 become VP (with NO NEEDLE OFF/ON as needed); G02/G03 become CR
    circular-arc commands. Comments are written as REM lines.
    """
    inv_scale = 1.0 / QliConverter.SCALE   # PAT → QLI
    lines: list[str] = []

    for comment in pat.comments:
        # ';' separates QLI commands, so it would turn the rest of the comment
        # into commands when the file is read back.
        text = (comment or "")
        for ch in ("\r", "\n", ";"):
            text = text.replace(ch, " ")
        text = text.strip()
        if text:
            lines.append(f"REM {text}")

    # QliConverter defaults to needle down (stitch) before any NO NEEDLE line.
    needle = 1
    cur_x = 0.0
    cur_y = 0.0

    for mv in pat.moves:
        if mv.gcode in (0, 1):
            want = 0 if mv.gcode == 0 else 1
            if want != needle:
                lines.append("NO NEEDLE OFF" if want == 0 else "NO NEEDLE ON")
                needle = want
            qx = mv.x * inv_scale
            qy = mv.y * inv_scale
            lines.append(f"VP {_fmt_qli_num(qx)},{_fmt_qli_num(qy)}")
            cur_x, cur_y = mv.x, mv.y

        elif mv.gcode in (2, 3) and mv.i is not None and mv.j is not None:
            # Arcs are always stitched in the QLI reader path.
            if needle != 1:
                lines.append("NO NEEDLE ON")
                needle = 1

            cx, cy = mv.i, mv.j
            r_pat = math.hypot(cur_x - cx, cur_y - cy)
            if r_pat < 1e-12:
                # Degenerate — fall back to a linear stitch.
                qx = mv.x * inv_scale
                qy = mv.y * inv_scale
                lines.append(f"VP {_fmt_qli_num(qx)},{_fmt_qli_num(qy)}")
                cur_x, cur_y = mv.x, mv.y
                continue

            start_deg = math.degrees(math.atan2(cur_y - cy, cur_x - cx))
            end_deg = math.degrees(math.atan2(mv.y - cy, mv.x - cx))
            # QLI: negative angle_range → G02 (CW); positive → G03 (CCW)
            from geometry import arc_sweep
            if mv.gcode == 3:
                sweep = arc_sweep(start_deg, end_deg, ccw=True)
            else:
                sweep = arc_sweep(start_deg, end_deg, ccw=False)

            r_qli = r_pat * inv_scale
            lines.append(
                f"CR {_fmt_qli_num(r_qli)},"
                f"{_fmt_qli_num(start_deg)},"
                f"{_fmt_qli_num(sweep)}"
            )
            cur_x, cur_y = mv.x, mv.y

        else:
            # Unknown / incomplete move — approximate with a linear VP.
            if needle != 1:
                lines.append("NO NEEDLE ON")
                needle = 1
            qx = mv.x * inv_scale
            qy = mv.y * inv_scale
            lines.append(f"VP {_fmt_qli_num(qx)},{_fmt_qli_num(qy)}")
            cur_x, cur_y = mv.x, mv.y

    lines.append("EN")

    with open(out_path, 'w', newline='', encoding='utf-8') as f:
        for line in lines:
            f.write(line + "\r\n")
    return out_path
