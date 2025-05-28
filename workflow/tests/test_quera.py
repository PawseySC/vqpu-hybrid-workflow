"""
@brief This example shows how to structure a multi-vqpu workflow.

This workflow spins up two or more vqpus and then has a workflow that runs cpu/gpu flows that also then spawn circuit flows.

"""

import io
import sys, os

sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/../")
import json
from vqpucommon.vqpubase import HybridQuantumWorkflowBase, SillyTestClass
from vqpucommon.vqpuquera import (
    quera_check_credentials,
    quera_parse_args,
    quera_check_qpu,
    quera_get_metadata,
    launch_quera_qpu_workflow,
)

from vqpucommon.utils import EventFile
from vqpucommon.clusters import get_dask_runners

# from circuits.braket_circuits import noisy_circuit
import asyncio
from prefect import task, flow
import unittest
import inspect


class TestHybridQueraWorkflowBasics(unittest.TestCase):
    cluster: str = "ella-qb-1.7.0"

    def test_quera_credentials(self):
        print(quera_check_credentials())

    def test_aws_braket_device_calls(self):
        devices = ["Aquila", "Gemini"]
        for d in devices:
            arguments: str = f"--queradevice={d}"
            print(f"Check if {d} available")
            result = asyncio.run(quera_check_qpu(arguments=arguments))
            print(result)
            result = asyncio.run(quera_get_metadata(arguments=arguments))
            print(result)

    def test_qpu_flow(self):
        frame = inspect.currentframe()
        # Get the function name
        function_name = frame.f_code.co_name
        # Get the line number
        line_number = frame.f_lineno
        arguments: str = "--queradevice=Aquila"
        print(f"Function name: {function_name}, Line number: {line_number}")
        # serializer = HybridQuantumWorkflowSerializer()
        myflow = HybridQuantumWorkflowBase(
            cluster=self.cluster,
            vqpu_ids=[1, 2, 3, 16],
        )
        asyncio.run(
            launch_quera_qpu_workflow.with_options(
                task_runner=myflow.gettaskrunner("generic-quera"),
            )(myqpuworkflow=myflow, qpu_id=1, arguments=arguments, walltime=10)
        )


if __name__ == "__main__":

    # if necessary, alter the cluster yaml configuration name
    TestHybridQueraWorkflowBasics.cluster = "ella-qb"
    unittest.main()
