from .base import SourceResult
from .ecb import fetch_ecb
from .fred import FRED_SERIES, fetch_fred
from .stablecoins import fetch_stablecoins

__all__ = ["SourceResult", "FRED_SERIES", "fetch_fred", "fetch_stablecoins", "fetch_ecb"]
