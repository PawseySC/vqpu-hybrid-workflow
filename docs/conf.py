# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

# make sure to add the path 
import os
import sys

packagepath = os.path.abspath('../') 
sys.path.insert(0, packagepath)

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
    'sphinx.ext.graphviz', # add graphviz visualisation 
]
todo_include_todos = True 
autodoc_mock_imports = [
    "asyncio", 
    "prefect", 
    "prefect_dask", 
    "dask_jobqueue", 
    "braket", 
    "bloqade",
    "pydantic"
    ]

templates_path = ['_templates']
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']

# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = 'nature'
html_static_path = ['_static']

# Control text wrapping in HTML output
html_theme_options = {
    'body_max_width': '90%',  # Adjust based on your theme
}

# For LaTeX output
latex_elements = {
    'papersize': 'letterpaper',
    'pointsize': '10pt',
    'preamble': r'''
        \usepackage[margin=1in]{geometry}
        \setlength{\parindent}{0pt}
    ''',
}