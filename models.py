"""
Core data classes shared by every file format reader/writer and the
canvas/thumbnail renderers: a single stitch/travel move, and a whole
pattern file (a list of moves plus header metadata).
"""

from dataclasses import dataclass, field
from typing import Optional

@dataclass
class Move:
    seq: int
    gcode: int
    x: float
    y: float
    i: Optional[float] = None
    j: Optional[float] = None
    is_stitch: bool = False

@dataclass
class PatFile:
    moves: list = field(default_factory=list)
    comments: list = field(default_factory=list)
    end_seq: Optional[int] = None
    filename: str = ""
