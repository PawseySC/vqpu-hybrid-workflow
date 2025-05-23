"""
@brief This example shows how to structure a multi-vqpu workflow.

This workflow spins up two or more vqpus and then has a workflow that runs cpu/gpu flows that also then spawn circuit flows.

"""

import io
import sys, os

sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/../")
import json
from vqpucommon.vqpubase import HybridQuantumWorkflowBase, SillyTestClass
from vqpucommon.vqpubraket import (
    aws_check_credentials,
    aws_braket_parse_args,
    aws_braket_check_qpu,
    aws_braket_get_metadata,
    launch_aws_braket_qpu_workflow,
)

# from vqpucommon.vqpubase import HybridQuantumWorkflowSerializer
from vqpucommon.vqpuflow import (
    cpu_workflow,
    gpu_workflow,
)
from vqpucommon.utils import EventFile
from vqpucommon.clusters import get_dask_runners

# from circuits.braket_circuits import noisy_circuit
import asyncio
from prefect import task, flow
import unittest
import inspect


class TestHybridAWSBraketWorkflowBasics(unittest.TestCase):
    cluster: str = "ella-qb-1.7.0"

    # def test_aws_braket_credentials(self):
    #     print(aws_check_credentials())

    # def test_aws_braket_device_calls(self):
    #     devices = ["Aquila", "Forte__1", "Aria__1", "Aria__2", "Ankaa-3"]
    #     for d in devices:
    #         arguments : str = f"--awsdevice={d}"
    #         print(f"Check if {d} available")
    #         result = asyncio.run(aws_braket_check_qpu(arguments=arguments))
    #         print(result)
    #         result = asyncio.run(aws_braket_get_metadata(arguments=arguments))
    #         print(result)

    def test_qpu_flow(self):
        frame = inspect.currentframe()
        # Get the function name
        function_name = frame.f_code.co_name
        # Get the line number
        line_number = frame.f_lineno
        arguments: str = "--awsdevice=Ankaa-3"
        print(f"Function name: {function_name}, Line number: {line_number}")
        # serializer = HybridQuantumWorkflowSerializer()
        myflow = HybridQuantumWorkflowBase(
            cluster=self.cluster,
            vqpu_ids=[1, 2, 3, 16],
        )
        asyncio.run(
            launch_aws_braket_qpu_workflow.with_options(
                task_runner=myflow.gettaskrunner("generic-aws"),
            )(myqpuworkflow=myflow, qpu_id=1, arguments=arguments, walltime=10)
        )


if __name__ == "__main__":

    # if necessary, alter the cluster yaml configuration name
    TestHybridAWSBraketWorkflowBasics.cluster = "ella-qb"
    unittest.main()
