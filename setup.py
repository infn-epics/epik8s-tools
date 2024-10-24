# setup.py
from setuptools import setup, find_packages

setup(
    name="epik8s-tools",
    version="0.2.0",
    packages=find_packages(),
    entry_points={
        'console_scripts': [
            'epik8s-gen=epik8s_gen.epik8s_gen:main',  # This registers the command `epik8s-gen`
        ],
    },
    install_requires=[
        
        'pyyaml','Jinja2'
        
        # Add any external dependencies here
    ],
    author="Andrea Michelotti",
    author_email="andrea.michelotti@infn.it",
    description="A set of tools for generating Kubernetes Helm charts for EPICS-based systems.",
    long_description=open('README.md').read(),
    long_description_content_type='text/markdown',
    url="https://github.com/yourusername/epik8s-tools",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.6',
)
