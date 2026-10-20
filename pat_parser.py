"""
Reader for native Innova .pat files.

PAT format summary:
  N<seq>G<code>X<x>Y<y>          - linear move
  N<seq>G<code>X<x>Y<y>I<i>J<j>  - arc move
  N<seq>M02                       - end of program
  ( comment )                     - comment line (after M02)
"""

import re

from models import Move, PatFile

# Numbers may omit the leading digit (".25", "-.5"); such moves used to be
# silently dropped.
_NUM = r'([+-]?(?:\d+\.?\d*|\.\d+))'
_MOVE_RE = re.compile(
    rf'N(\d+)\s*G(\d+)\s*X{_NUM}\s*Y{_NUM}'
    rf'(?:\s*I{_NUM}\s*J{_NUM})?',
    re.IGNORECASE
)
_END_RE  = re.compile(r'N(\d+)\s*M02', re.IGNORECASE)
_CMNT_RE = re.compile(r'^\s*\(\s*(.*?)\s*\)\s*$')


def parse_pat(path: str) -> PatFile:
    pat = PatFile(filename=path)
    with open(path, 'r', encoding='utf-8', errors='replace') as fh:
        for raw in fh:
            line = raw.strip()
            m = _END_RE.search(line)
            if m:
                pat.end_seq = int(m.group(1))
                continue
            m = _CMNT_RE.match(line)
            if m:
                pat.comments.append(m.group(1))
                continue
            m = _MOVE_RE.search(line)
            if m:
                seq   = int(m.group(1))
                gcode = int(m.group(2))
                x     = float(m.group(3))
                y     = float(m.group(4))
                i     = float(m.group(5)) if m.group(5) is not None else None
                j     = float(m.group(6)) if m.group(6) is not None else None
                pat.moves.append(Move(seq, gcode, x, y, i, j,
                                      is_stitch=(gcode in (1, 2, 3))))
    return pat
