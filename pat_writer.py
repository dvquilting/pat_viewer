"""
Writes a PatFile object out to disk in Innova PAT format.
Shared by the SVG->PAT converter and the QLI->PAT "Convert" menu action.
"""

from models import PatFile


def _comment_text(comment: str) -> str:
    """
    A comment that stays on one line inside a single pair of parentheses, so
    its text can never be read back as a command.
    """
    text = (comment or "").replace("\r", " ").replace("\n", " ")
    return text.replace("(", "[").replace(")", "]")


def _write_pat_file(pat: PatFile, out_path: str):
    """
    Write a PatFile object to disk in Innova PAT format.
    Lines are terminated with CR+LF to match the machine's expected format.

    An arc without a centre is drawn as a straight stitch, so it is written
    as G01; other G-codes are kept as they are rather than dropped.
    """
    with open(out_path, 'w', newline='') as f:
        for mv in pat.moves:
            arc = mv.gcode in (2, 3)
            centred = arc and mv.i is not None and mv.j is not None
            gcode = 1 if arc and not centred else mv.gcode
            line = f"N{mv.seq:02d}G{gcode:02d}X{mv.x:.4f}Y{mv.y:.4f}"
            if centred:
                line += f"I{mv.i:.4f}J{mv.j:.4f}"
            f.write(line + "\r\n")
        if pat.end_seq:
            f.write(f"N{pat.end_seq:02d}M02\r\n")
        for c in pat.comments:
            f.write(f"( {_comment_text(c)} )\r\n")
