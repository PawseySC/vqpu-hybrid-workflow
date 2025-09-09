"""
@brief This example shows how to structure a multi-vqpu workflow.

This workflow spins up two or more vqpus and then has a workflow that runs cpu/gpu flows that also then spawn circuit flows.

"""

import io
import sys, os

sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/../")
sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/../../")
import json
from qbitbridge.vqpubase import HybridQuantumWorkflowBase, SillyTestClass
from qbitbridge.vqpuqiskit import (
    qiskit_check_credentials,
    qiskit_parse_args,
    qiskit_check_qpu,
    qiskit_get_metadata,
    launch_qiskit_qpu_workflow,
    qiskit_allowed_devices
)

from qbitbridge.utils import EventFile
from qbitbridge.clusters import get_dask_runners
from qiskit_runtime import QiskitRuntimeService

# from circuits.braket_circuits import noisy_circuit
import asyncio
from prefect import task, flow
import unittest
import inspect
from typing import List, Tuple


class TestHybridQueraWorkflowBasics(unittest.TestCase):
    cluster: str = "ella-qb-1.7.0"
    account_info : Tuple[str,str] | None = None

    def test_qiskit_credentials(self):
        print(qiskit_check_credentials(account_info=self.account_info))

    def test_qiskit_device_calls(self):
        qiskit_service = QiskitRuntimeService()
        qiskit_devices: List = []
        for backend in qiskit_service.backends():
            qiskit_devices.append(backend.config_name)
        
        for d in qiskit_devices:
            arguments: str = f"--qiskitdevice={d}"
            print(f"Check if {d} available")
            result = asyncio.run(qiskit_check_qpu(arguments=arguments))
            print(result)
            result = asyncio.run(qiskit_get_metadata(arguments=arguments))
            print(result)

    def test_qpu_flow(self):
        frame = inspect.currentframe()
        # Get the function name
        function_name = frame.f_code.co_name
        # Get the line number
        line_number = frame.f_lineno
        qiskit_service = QiskitRuntimeService()
        qiskit_devices: List = []
        for backend in qiskit_service.backends():
            qiskit_devices.append(backend.config_name)
        arguments: str = f"--qiskitdevice={qiskit_devices[0]}"
        print(f"Function name: {function_name}, Line number: {line_number}")
        # serializer = HybridQuantumWorkflowSerializer()
        myflow = HybridQuantumWorkflowBase(
            cluster=self.cluster,
            vqpu_ids=[1, 2, 3, 16],
        )
        asyncio.run(
            launch_qiskit_qpu_workflow.with_options(
                task_runner=myflow.gettaskrunner("circuit-qiskit"),
            )(myqpuworkflow=myflow, qpu_id=1, arguments=arguments, walltime=10)
        )


if __name__ == "__main__":

    # if necessary, alter the cluster yaml configuration name
    TestHybridQueraWorkflowBasics.cluster = "ella-qb"
    unittest.main()
