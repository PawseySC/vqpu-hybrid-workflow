.. _installation:

Installation
############

The framework does not need to be explicitly installed in the python path. 
However, we strongly recommend using a python virtual environment to install all 
the dependencies. 

You can use the :file:`pyproject.toml` and pip to install the package:

.. code-block::

  # Install dependencies with its pyproject.toml configuration
  pip install --deps-only .

  # Install in development mode
  pip install -e .

Other packages 
==============

For testing, there is a :file:`test_basic_flow.py` in the test directory that 
makes use of a `profile_util` package. This is a MPI+OpenMP+CUDA/HIP C++ code that requires 

* CMake (>3.20)
* C++ (c++17)
* OpenMP (optional)
* CUDA or HIP (optional)
* MPI (optional)

The package can be built running the :file:`build_cuda.sh` and :file:`build_hip.sh` scripts provided 
in the `profile_util` package. These use cmake to build a GPU-enabled, OpenMP-enabled code that can be 
used to test GPU and CPU flows. 
