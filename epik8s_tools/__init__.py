# __init__.py
from .epik8s_version import __version__
from .opigen import main_opigen
# Import primary functions for external use
from .epik8s_gen import main, render_template, load_values_yaml, create_directory_tree

__all__ = [
    "main",
    "main_opigen",
    "render_template",
    "load_values_yaml",
    "create_directory_tree",
    "__version__"
]
__author__ = "Andrea Michelotti"
