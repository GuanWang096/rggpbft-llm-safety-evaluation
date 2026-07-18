import sys
from pathlib import Path


SRC_DIR = Path(__file__).parents[1]
sys.path.insert(0, str(SRC_DIR))
sys.path.insert(0, str(SRC_DIR / "rggpbft_distributed"))
