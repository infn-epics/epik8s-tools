# __init__.py

# Import primary functions for external use
from .epik8s_gen import main, render_template, load_values_yaml, create_directory_tree,__version__

__all__ = [
    "main",
    "render_template",
    "load_values_yaml",
    "create_directory_tree"
]

# Optional: Define package metadata
__author__ = "Andrea Michelotti"
