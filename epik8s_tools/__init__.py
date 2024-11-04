# __init__.py

from .opigen import main_opigen
# Import primary functions for external use
from .epik8s_gen import main, render_template, load_values_yaml, create_directory_tree,__version__

__all__ = [
    "main",
    "main_opigen",
    "render_template",
    "load_values_yaml",
    "create_directory_tree"
]

# Optional: Define package metadata
__author__ = "Andrea Michelotti"
