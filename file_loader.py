"""
Format-dispatching loader: given any .pat/.qli/.svg path, returns a
PatFile using whichever converter matches the extension.
"""

from models import PatFile
from pat_parser import parse_pat
from qli_converter import QliConverter
from svg_converter import _svg_to_pat

def load_file(path: str) -> PatFile:
    """Load a .PAT, .QLI, or .SVG file and return a PatFile."""
    ext = path.rsplit('.', 1)[-1].lower()
    if ext == 'qli':
        return QliConverter().convert(path)
    elif ext == 'svg':
        return _svg_to_pat(path)
    else:
        return parse_pat(path)
