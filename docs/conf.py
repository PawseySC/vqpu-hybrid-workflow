# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

# make sure to add the path 
import os
import sys

packagepath = os.path.abspath('../workflow/') 
sys.path.insert(0, packagepath)
# pypath = os.getenv("PYTHONPATH")
# os.environ["PYTHONPATH"] = f"${pypath}:{packagepath}"
# sys.path.insert(0, os.path.abspath('../workflow/'))
# sys.path.insert(0, os.path.abspath('../workflow/vqpucommon'))

project = 'QBitBridge'
copyright = '2025, Pascal Jahan Elahi'
author = 'Pascal Jahan Elahi'
release = '0.1.1'

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    'sphinx.ext.autodoc', #Allows sphinx-apidoc to work 
    'sphinx.ext.todo', #optional.  Allows inline "todo:"
    'sphinx.ext.imgmath', #optional. Allows LaTeX equations 
    'sphinx.ext.napoleon', #Allows google/numpy docstrings
    'sphinx.ext.githubpages', #Adds .nojekyll file
    'sphinx.ext.viewcode', 
]
todo_include_todos = True 
autodoc_mock_imports = [
    "asyncio", 
    "prefect", 
    "prefect_dask", 
    "dask_jobqueue", 
    "braket", 
    "bloqade", 
    # "vqpucommon.options",
    # "vqpucommon.utils",
    # "vqpucommon.vqpubase",
    # "vqpucommon", 
    # "vqpubase", 
    # "vqpuflow", 
    # "vqpubraket", 
    # "vqpuquera",
    # "EventFile",
    ]

templates_path = ['_templates']
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']



# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = 'alabaster'
html_static_path = ['_static']
