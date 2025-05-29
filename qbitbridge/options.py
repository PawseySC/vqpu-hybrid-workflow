"""
options class for handling arguments of workflow. Not yet implemented
"""

from datetime import datetime
from pydantic import BaseModel, ValidationError


class vQPUWorkflow(BaseModel):
    """
    Arguments to workflow
    """

    nqubits: int = 2
    """Number of qubits"""
    quantum_kernels_file: str = ""
    """File containing quantum kernel(s) to run"""
