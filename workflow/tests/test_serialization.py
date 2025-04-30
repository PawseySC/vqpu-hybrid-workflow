"""
@brief This example shows how to structure a multi-vqpu workflow.

This workflow spins up two or more vqpus and then has a workflow that runs cpu/gpu flows that also then spawn circuit flows.

"""

import io
import sys, os

sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/../")
import json
from vqpucommon.vqpubase import HybridQuantumWorkflowBase, SillyTestClass

# from vqpucommon.vqpubase import HybridQuantumWorkflowSerializer
from vqpucommon.vqpuflow import (
    launch_vqpu_workflow,
    cpu_workflow,
    gpu_workflow,
    FlowForSillyTestClass,
    circuits_vqpu_workflow,
)
from vqpucommon.utils import EventFile
from vqpucommon.clusters import get_dask_runners
from circuits.qristal_circuits import simulator_setup, noisy_circuit
import asyncio
from prefect import task, flow
import unittest
import inspect


class SillyClass:
    y: int = 0

    def __init__(self, x=2):
        self.x = x


@task()
def SillyTask(obj1: SillyClass, obj2: SillyClass):
    obj1.x += 2
    obj2.x -= 1
    obj1.y = 1
    obj2.y = 2


@flow()
def SillyFlow(baseobj: SillyClass | None = None):
    if baseobj != None:
        baseobj.x = baseobj.y
    obj1 = SillyClass(x=100)
    obj2 = SillyClass(x=0)
    future = SillyTask.submit(obj1=obj1, obj2=obj2)
    future.result()


@flow(
    name="Multi-vQPU Test",
    description="Running a multi-(v)QPU+CPU+GPU hybrid workflow",
    retries=3,
    retry_delay_seconds=10,
    log_prints=True,
)
async def multivqpuworkflow(
    myqpuworkflow: HybridQuantumWorkflowBase,
    circuitargs: str,
):
    circflow = circuits_vqpu_workflow.with_options(
        task_runner=myqpuworkflow.gettaskrunner("circuit"),
    )
    vqpuflows = dict()
    circuits = dict()
    for vqpu_id in myqpuworkflow.vqpu_ids:
        vqpuflows[f"vqpu_{vqpu_id}"] = launch_vqpu_workflow.with_options(
            task_runner=myqpuworkflow.gettaskrunner("vqpu"),
        )
    for vqpu_id in myqpuworkflow.vqpu_ids:
        circuits[f"vqpu_{vqpu_id}"] = [noisy_circuit, noisy_circuit, noisy_circuit]

    async with asyncio.TaskGroup() as tg:
        for vqpu_id in myqpuworkflow.vqpu_ids:
            tg.create_task(
                vqpuflows[f"vqpu_{vqpu_id}"](
                    myqpuworkflow=myqpuworkflow,
                    walltime=200,
                    vqpu_id=vqpu_id,
                )
            )
        for vqpu_id in myqpuworkflow.vqpu_ids:
            tg.create_task(
                circflow(
                    myqpuworkflow=myqpuworkflow,
                    vqpu_id=vqpu_id,
                    circuits=circuits[f"vqpu_{vqpu_id}"],
                    arguments=circuitargs,
                    circuits_complete=True,
                )
            )


class TestHybridWorkflowBasics(unittest.TestCase):
    cluster: str = "ella-qb"

    def test_jsonserialization(self):
        frame = inspect.currentframe()
        # Get the function name
        function_name = frame.f_code.co_name
        # Get the line number
        line_number = frame.f_lineno
        print(f"Function name: {function_name}, Line number: {line_number}")

        myflow = HybridQuantumWorkflowBase(
            cluster=self.cluster,
            vqpu_ids=[1, 2, 3, 16],
        )
        # Serialize the object to a JSON string
        json_string = json.dumps(myflow.to_dict())
        # print('Serialized JSON:', json_string)
        # Deserialize the JSON string back into an object
        loaded_object = HybridQuantumWorkflowBase.from_dict(json.loads(json_string))
        print("Check if json serialization of object works")
        self.assertEqual(myflow, loaded_object)

    # def test_serialize(self):
    #     frame = inspect.currentframe()
    #     # Get the function name
    #     function_name = frame.f_code.co_name
    #     # Get the line number
    #     line_number = frame.f_lineno
    #     print(f"Function name: {function_name}, Line number: {line_number}")
    #     serializer = HybridQuantumWorkflowSerializer()
    #     myflow = HybridQuantumWorkflowBase(
    #         cluster = self.cluster,
    #         vqpu_ids = [1, 2, 3, 16],
    #     )
    #     serialized = serializer.serialize(myflow)
    #     print('Check if serialization using custom serializer works')
    #     self.assertEqual(serialized, myflow.to_dict())

    # def test_serialize_on_arbitrary_class(self):
    #     frame = inspect.currentframe()
    #     # Get the function name
    #     function_name = frame.f_code.co_name
    #     # Get the line number
    #     line_number = frame.f_lineno
    #     print(f"Function name: {function_name}, Line number: {line_number}")
    #     serializer = HybridQuantumWorkflowSerializer()
    #     obj  = SillyClass(x = 100)
    #     print('Check if serialization using custom serializer works on simply class not explicitly listed')
    #     # serialized = serializer.serialize(obj)
    #     x : float = 1.0
    #     y = { 'foo' : [1,2,4,5], 'bar': 'what?'}
    #     serialized = serializer.serialize(y)
    #     deserialzed = serializer.deserialize(serialized)
    #     self.assertEqual(y, deserialzed)

    # def test_deserialize(self):
    #     frame = inspect.currentframe()
    #     # Get the function name
    #     function_name = frame.f_code.co_name
    #     # Get the line number
    #     line_number = frame.f_lineno
    #     print(f"Function name: {function_name}, Line number: {line_number}")
    #     serializer = HybridQuantumWorkflowSerializer()
    #     myflow = HybridQuantumWorkflowBase(
    #         cluster = self.cluster,
    #         vqpu_ids = [1, 2, 3, 16],
    #     )
    #     data = myflow.to_dict()  # Use a dictionary representing the serialized form
    #     obj = serializer.deserialize(data)
    #     print('Check if deserialization using custom serializer works')
    #     self.assertIsInstance(obj, HybridQuantumWorkflowBase)

    def test_flowwithlocalclass(self):
        frame = inspect.currentframe()
        # Get the function name
        function_name = frame.f_code.co_name
        # Get the line number
        line_number = frame.f_lineno
        print(f"Function name: {function_name}, Line number: {line_number}")
        print("Check simple flow with silly class works")
        SillyFlow()

    def test_flowwithclass(self):
        frame = inspect.currentframe()
        # Get the function name
        function_name = frame.f_code.co_name
        # Get the line number
        line_number = frame.f_lineno
        print(f"Function name: {function_name}, Line number: {line_number}")
        print(
            "Check simple flow with SillyTestClass defined in vqpucommon.vqpuworkflow works"
        )
        FlowForSillyTestClass()

    def test_flowwithlocalrunner(self):
        frame = inspect.currentframe()
        # Get the function name
        function_name = frame.f_code.co_name
        # Get the line number
        line_number = frame.f_lineno
        print(f"Function name: {function_name}, Line number: {line_number}")
        myflow = HybridQuantumWorkflowBase(
            cluster=self.cluster,
            vqpu_ids=[1, 2, 3, 16],
        )
        print(
            "Check if simple flow without vQPU related classes with local task runner works"
        )
        asyncio.run(
            cpu_workflow(myqpuworkflow=myflow, execs=["ls"], arguments=["/opt/"])
        )

    def test_flowwithclassanddaskrunner(self):
        frame = inspect.currentframe()
        # Get the function name
        function_name = frame.f_code.co_name
        # Get the line number
        line_number = frame.f_lineno
        print(f"Function name: {function_name}, Line number: {line_number}")
        print(
            "Check simple flow with SillyTestClass defined in vqpucommon.vqpuworkflow works"
        )
        # task_runners = get_dask_runners(self.cluster)
        # myflow = FlowForSillyTestClass.with_options(task_runner = task_runners['cpu'])
        # myflow()
        obj = SillyTestClass(cluster=self.cluster)
        task_runner = obj.gettaskrunner("cpu")
        myflow = FlowForSillyTestClass.with_options(task_runner=task_runner)
        myflow(baseobj=obj)

    def test_flowwithdasktaskrunner(self):
        frame = inspect.currentframe()
        # Get the function name
        function_name = frame.f_code.co_name
        # Get the line number
        line_number = frame.f_lineno
        print(f"Function name: {function_name}, Line number: {line_number}")
        myflow = HybridQuantumWorkflowBase(
            cluster=self.cluster,
            vqpu_ids=[1, 2, 3, 16],
        )
        # cpuflow = cpu_workflow.with_options(task_runner = myflow.taskrunners['cpu'])
        cpuflow = cpu_workflow.with_options(task_runner=myflow.gettaskrunner("cpu"))
        print(
            "Check if simple flow without vQPU related classes with dask task runner works"
        )
        asyncio.run(cpuflow(execs=["ls"], arguments=["/opt/"]))

    def test_flow(self):
        frame = inspect.currentframe()
        # Get the function name
        function_name = frame.f_code.co_name
        # Get the line number
        line_number = frame.f_lineno
        print(f"Function name: {function_name}, Line number: {line_number}")
        myflow = HybridQuantumWorkflowBase(
            cluster=self.cluster,
            vqpu_ids=[1, 2, 3, 16],
        )
        # cpuflow = cpu_workflow.with_options(task_runner = myflow.taskrunners['cpu'])
        cpuflow = cpu_workflow.with_options(task_runner=myflow.gettaskrunner("cpu"))
        print(
            "Check if simple flow without vQPU related classes with dask task runner works"
        )
        asyncio.run(
            cpuflow(myqpuworkflow=myflow, execs=["ls"], arguments=["/usr/local/"])
        )

    def test_gpu_flow(self):
        frame = inspect.currentframe()
        # Get the function name
        function_name = frame.f_code.co_name
        # Get the line number
        line_number = frame.f_lineno
        print(f"Function name: {function_name}, Line number: {line_number}")
        myflow = HybridQuantumWorkflowBase(
            cluster=self.cluster,
            vqpu_ids=[1, 2, 3, 16],
        )
        # cpuflow = cpu_workflow.with_options(task_runner = myflow.taskrunners['cpu'])
        cpuflow = gpu_workflow.with_options(task_runner=myflow.gettaskrunner("gpu"))
        print(
            "Check if simple flow without vQPU related classes with dask task runner works"
        )
        asyncio.run(
            cpuflow(
                myqpuworkflow=myflow,
                execs=["nvidia-smi", "nvidia-smi", "nvidia-smi"],
                arguments=["", "", ""],
            )
        )

    def test_vqpu_flow(self):
        frame = inspect.currentframe()
        # Get the function name
        function_name = frame.f_code.co_name
        # Get the line number
        line_number = frame.f_lineno
        print(f"Function name: {function_name}, Line number: {line_number}")
        # serializer = HybridQuantumWorkflowSerializer()
        myflow = HybridQuantumWorkflowBase(
            cluster=self.cluster,
            vqpu_ids=[1, 2, 3, 16],
        )
        asyncio.run(
            launch_vqpu_workflow.with_options(
                task_runner=myflow.gettaskrunner("vqpu"),
            )(myqpuworkflow=myflow, vqpu_id=1, walltime=10)
        )

    def test_multivqpu_flow(self):
        frame = inspect.currentframe()
        # Get the function name
        function_name = frame.f_code.co_name
        # Get the line number
        line_number = frame.f_lineno
        print(f"Function name: {function_name}, Line number: {line_number}")
        myflow = HybridQuantumWorkflowBase(
            cluster=self.cluster,
            vqpu_ids=[1, 2, 3, 16],
        )
        asyncio.run(
            launch_vqpu_workflow.with_options(
                task_runner=myflow.gettaskrunner("vqpu"),
            )(myqpuworkflow=myflow, vqpu_id=1, walltime=10)
        )


if __name__ == "__main__":

    # if necessary, alter the cluster yaml configuration name
    TestHybridWorkflowBasics.cluster = "ella-qb"
    unittest.main()
