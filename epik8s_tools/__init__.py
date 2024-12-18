# __init__.py
from .opigen import main_opigen
from .epik8s_compose import main_compose
# Import primary functions for external use
from .epik8s_gen import main
from .epik8s_version import __version__

__all__ = [
    "main",
    "main_opigen"
]
__author__ = "Andrea Michelotti"
