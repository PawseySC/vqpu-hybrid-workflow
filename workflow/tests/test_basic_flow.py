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

# from qbitbridge.vqpubase import HybridQuantumWorkflowSerializer
from qbitbridge.vqpuflow import (
    launch_vqpu_workflow,
    cpu_workflow,
    gpu_workflow,
    FlowForSillyTestClass,
    circuits_vqpu_workflow,
    limit_concurrent_tasks,
    run_tasks_with_concurrency_limit,
)
from qbitbridge.utils import EventFile, get_num_gpus
from qbitbridge.clusters import get_dask_runners
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
    cluster: str = "ella-qb-1.7.0-pypath"
    vqpu_template_script: str = (
        os.path.dirname(os.path.abspath(__file__))
        + "/../qb-vqpu/vqpu_template_ella_qpu-1.7.0.sh"
    )
    vqpu_template_yaml: str = (
        os.path.dirname(os.path.abspath(__file__))
        + "/../qb-vqpu/remote_vqpu_ella_template.yaml"
    )
    gpuruns: int = 4
    gpucudaexec: str = (
        os.path.dirname(os.path.abspath(__file__))
        + "/profile_util/build-cuda/src/tests/test_profile_util"
    )
    gpuhipexec: str = (
        os.path.dirname(os.path.abspath(__file__))
        + "/profile_util/build-hip/src/tests/test_profile_util"
    )
    gpuexec: str = ""

    def test_jsonserialization(self):
        """Test Serialization."""
        frame = inspect.currentframe()
        # Get the function name
        function_name = frame.f_code.co_name
        # Get the line number
        line_number = frame.f_lineno
        print(f"Function name: {function_name}, Line number: {line_number}")

        myflow = HybridQuantumWorkflowBase(
            cluster=self.cluster,
            vqpu_ids=[1, 2, 3, 16],
            vqpu_template_script=self.vqpu_template_script,
            vqpu_template_yaml=self.vqpu_template_yaml,
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
        """Test basic prefect flow to see that it is all working."""
        frame = inspect.currentframe()
        # Get the function name
        function_name = frame.f_code.co_name
        # Get the line number
        line_number = frame.f_lineno
        print(f"Function name: {function_name}, Line number: {line_number}")
        print("Check simple flow with silly class works")
        SillyFlow()

    def test_flowwithclass(self):
        """Test flow with class being serialized."""
        frame = inspect.currentframe()
        # Get the function name
        function_name = frame.f_code.co_name
        # Get the line number
        line_number = frame.f_lineno
        print(f"Function name: {function_name}, Line number: {line_number}")
        print(
            "Check simple flow with SillyTestClass defined in qbitbridge.vqpuworkflow works"
        )
        FlowForSillyTestClass()

    def test_flowwithlocalrunner(self):
        """Test flow with HybridQuantumWorlfowBase class and local task runners."""
        frame = inspect.currentframe()
        # Get the function name
        function_name = frame.f_code.co_name
        # Get the line number
        line_number = frame.f_lineno
        print(f"Function name: {function_name}, Line number: {line_number}")
        myflow = HybridQuantumWorkflowBase(
            cluster=self.cluster,
            vqpu_ids=[1, 2, 3, 16],
            vqpu_template_script=self.vqpu_template_script,
            vqpu_template_yaml=self.vqpu_template_yaml,
        )
        print(
            "Check if simple flow without vQPU related classes with local task runner works"
        )
        asyncio.run(
            cpu_workflow(myqpuworkflow=myflow, execs=["ls"], arguments=["/opt/"])
        )

    def test_flowwithclassanddaskrunner(self):
        """Test flow with a class and dask task runners."""
        frame = inspect.currentframe()
        # Get the function name
        function_name = frame.f_code.co_name
        # Get the line number
        line_number = frame.f_lineno
        print(f"Function name: {function_name}, Line number: {line_number}")
        print(
            "Check simple flow with SillyTestClass defined in qbitbridge.vqpuworkflow works"
        )
        # task_runners = get_dask_runners(self.cluster)
        # myflow = FlowForSillyTestClass.with_options(task_runner = task_runners['cpu'])
        # myflow()
        obj = SillyTestClass(cluster=self.cluster)
        task_runner = obj.gettaskrunner("cpu")
        myflow = FlowForSillyTestClass.with_options(task_runner=task_runner)
        myflow(baseobj=obj)

    def test_flowwithdasktaskrunner(self):
        """Test flow with dask task runners."""
        frame = inspect.currentframe()
        # Get the function name
        function_name = frame.f_code.co_name
        # Get the line number
        line_number = frame.f_lineno
        print(f"Function name: {function_name}, Line number: {line_number}")
        myflow = HybridQuantumWorkflowBase(
            cluster=self.cluster,
            vqpu_ids=[1, 2, 3, 16],
            vqpu_template_script=self.vqpu_template_script,
            vqpu_template_yaml=self.vqpu_template_yaml,
        )
        # cpuflow = cpu_workflow.with_options(task_runner = myflow.taskrunners['cpu'])
        cpuflow = cpu_workflow.with_options(task_runner=myflow.gettaskrunner("cpu"))
        print(
            "Check if simple flow without vQPU related classes with dask task runner works"
        )
        asyncio.run(cpuflow(execs=["ls"], arguments=["/opt/"]))

    def test_flow(self):
        """Test flow with HybridQuantumWorlfowBase class and dask task runners."""
        frame = inspect.currentframe()
        # Get the function name
        function_name = frame.f_code.co_name
        # Get the line number
        line_number = frame.f_lineno
        print(f"Function name: {function_name}, Line number: {line_number}")
        myflow = HybridQuantumWorkflowBase(
            cluster=self.cluster,
            vqpu_ids=[1, 2, 3, 16],
            vqpu_template_script=self.vqpu_template_script,
            vqpu_template_yaml=self.vqpu_template_yaml,
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
        """Test flow with HybridQuantumWorlfowBase class and daks task runners, gpu focus.
        This test is currently requires that profile_util's gpu code is built.
        To enable this please enter the profile_util dire and run ./build_cuda.sh. This assumes
        cmake, and nvc++. if on AMD, ensure ./build_hip.sh is run.
        There is currently some issues with too many open files might have to do with the polling and the
        nodes being used to run this test.
        """
        frame = inspect.currentframe()
        # Get the function name
        function_name = frame.f_code.co_name
        # Get the line number
        line_number = frame.f_lineno
        print(f"Function name: {function_name}, Line number: {line_number}")
        myflow = HybridQuantumWorkflowBase(
            cluster=self.cluster,
            vqpu_ids=[1, 2, 3, 16],
            vqpu_template_script=self.vqpu_template_script,
            vqpu_template_yaml=self.vqpu_template_yaml,
        )

        gpuflow = gpu_workflow.with_options(task_runner=myflow.gettaskrunner("gpu"))
        ngpus, gputype = get_num_gpus()
        if gputype == "NVIDIA":
            self.gpuexec = self.gpucudaexec
        elif gputype == "AMD":
            self.gpuexec = self.gpuhipexec

        execs = [self.gpuexec for i in range(self.gpuruns)]
        arugments = ["" for i in range(self.gpuruns)]
        print(
            "Check if simple GPU flow without vQPU related classes with dask task runner works"
        )
        asyncio.run(
            gpuflow(
                myqpuworkflow=myflow,
                execs=execs,
                arguments=arugments,
            )
        )

    def test_vqpu_flow(self):
        """Test flow with HybridQuantumWorlfowBase class and dask task runners launching vQPU."""
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
            vqpu_template_script=self.vqpu_template_script,
            vqpu_template_yaml=self.vqpu_template_yaml,
        )
        asyncio.run(
            launch_vqpu_workflow.with_options(
                task_runner=myflow.gettaskrunner("vqpu"),
            )(myqpuworkflow=myflow, vqpu_id=1, walltime=10)
        )

    def test_limit_concurrent(self):
        """Test flow with limited tasks running concurrently."""
        @limit_concurrent_tasks(max_active_task=5, sleep_time_submission = 10, sleep_time_active_tasks_poll=20, max_task_submissions=3)
        @task
        def process_data(item):
            x = int(item)
            # Your processing logic
            return x * 2

        @flow
        def limited_flow():
            items = list(range(5))
            results = process_data(items)
            print("Processed results:", results)
        @task
        def process_data2(item):
            x = int(item)
            # Your processing logic
            return x * 3

        @task
        def process_data3(item):
            x = int(item)
            # Your processing logic
            return f"now trying other stuff {x * 4}"

        @flow
        def limited_flow2():
            tasks = [process_data2 for i in range(10)] + [process_data3 for i in range(10)]
            args = list(range(len(tasks)))
            results = run_tasks_with_concurrency_limit(
                task_func_wrapper=tasks, 
                args=args, 
                max_task_submissions=3,
                max_active_task=15,
                sleep_time_submission=4,
                sleep_time_active_tasks_poll=100,
            )
            print("Flow results:", results)

        myflow = HybridQuantumWorkflowBase(
            cluster=self.cluster,
            vqpu_ids=[1, 2, 3, 16],
            vqpu_template_script=self.vqpu_template_script,
            vqpu_template_yaml=self.vqpu_template_yaml,
        )
        task_runner = myflow.gettaskrunner("cpu")
        limitedflow = limited_flow.with_options(task_runner=task_runner)
        limitedflow()
        limitedflow = limited_flow2.with_options(task_runner=task_runner)
        limitedflow()
       

if __name__ == "__main__":

    # if necessary, alter the cluster yaml configuration name
    TestHybridWorkflowBasics.cluster = "ella-qb-1.7.0-pypath"
    unittest.main()
