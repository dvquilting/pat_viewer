"""
Writes a PatFile object out to disk in Innova PAT format.
Shared by the SVG->PAT converter and the QLI->PAT "Convert" menu action.
"""

from models import PatFile

def _write_pat_file(pat: PatFile, out_path: str):
    """
    Write a PatFile object to disk in Innova PAT format.
    Lines are terminated with CR+LF to match the machine's expected format.
    """
    with open(out_path, 'w', newline='') as f:
        for mv in pat.moves:
            if mv.gcode == 0:
                f.write(f"N{mv.seq:02d}G00X{mv.x:.4f}Y{mv.y:.4f}\r\n")
            elif mv.gcode == 1:
                f.write(f"N{mv.seq:02d}G01X{mv.x:.4f}Y{mv.y:.4f}\r\n")
            elif (mv.gcode in (2, 3)
                  and mv.i is not None and mv.j is not None):
                f.write(f"N{mv.seq:02d}G0{mv.gcode}"
                        f"X{mv.x:.4f}Y{mv.y:.4f}"
                        f"I{mv.i:.4f}J{mv.j:.4f}\r\n")
        if pat.end_seq:
            f.write(f"N{pat.end_seq:02d}M02\r\n")
        for c in pat.comments:
            f.write(f"( {c} )\r\n")
