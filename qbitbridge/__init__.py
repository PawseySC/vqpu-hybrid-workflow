# qbitbridge/__init__.py
# (might add some default loads)

from . import clusters
from . import options
from . import utils

from . import vqpubase
from . import vqpuflow
try:
    from . import vqpubraket
    HAS_BRAKET = True
except ImportError:
    vqpubraket = None
    HAS_BRAKET = False
try:
    from . import vqpuquera
    HAS_QUERA = True
except ImportError:
    vqpuquera = None
    HAS_QUERA = False
try:
    from . import vqpuqiskit
    HAS_QISKIT = True
except ImportError:
    vqpuqiskit = None
    HAS_QISKIT = False
try:
    from . import vqpucudaq
    HAS_CUDAQ = True
except ImportError:
    vqpucudaq = None
    HAS_CUDAQ = False

__all__ = [
    "clusters",
    "options",
    "utils",
    "vqpubase",
    "vqpuflow",
    "vqpubraket",
    "HAS_BRAKET",
    "vqpuquera",
    "HAS_QUERA",
    "vqpuqiskit",
    "HAS_QISKIT",
    "vqpucudaq",
    "HAS_CUDAQ",
]
