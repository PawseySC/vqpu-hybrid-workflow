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
from qbitbridge.vqpucudaq import (
    cudaq_check_credentials,
    cudaq_parse_args,
    cudaq_check_qpu,
    cudaq_get_metadata,
    launch_cudaq_qpu_workflow,
    cudaq_allowed_devices
)

from qbitbridge.utils import EventFile
from qbitbridge.clusters import get_dask_runners

# from circuits.braket_circuits import noisy_circuit
import asyncio
from prefect import task, flow
import unittest
import inspect


class TestHybridQueraWorkflowBasics(unittest.TestCase):
    cluster: str = "ella-qb-1.7.0"

    def test_cudaq_credentials(self):
        print(cudaq_check_credentials())

    def test_cudaq_device_calls(self):
        
        for d in cudaq_allowed_devices:
            arguments: str = f"--cudaqdevice={d}"
            print(f"Check if {d} available")
            result = asyncio.run(cudaq_check_qpu(arguments=arguments))
            print(result)
            result = asyncio.run(cudaq_get_metadata(arguments=arguments))
            print(result)

    def test_qpu_flow(self):
        frame = inspect.currentframe()
        # Get the function name
        function_name = frame.f_code.co_name
        # Get the line number
        line_number = frame.f_lineno
        arguments: str = "--cudaqdevice=simulator"
        print(f"Function name: {function_name}, Line number: {line_number}")
        # serializer = HybridQuantumWorkflowSerializer()
        myflow = HybridQuantumWorkflowBase(
            cluster=self.cluster,
            vqpu_ids=[1, 2, 3, 16],
        )
        asyncio.run(
            launch_cudaq_qpu_workflow.with_options(
                task_runner=myflow.gettaskrunner("circuit-cudaq"),
            )(myqpuworkflow=myflow, qpu_id=1, arguments=arguments, walltime=10)
        )


if __name__ == "__main__":

    # if necessary, alter the cluster yaml configuration name
    TestHybridQueraWorkflowBasics.cluster = "ella-qb"
    unittest.main()
