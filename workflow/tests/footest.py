import io
import sys, os

sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/../")
sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/../../")
import json
from qbitbridge.vqpubase import HybridQuantumWorkflowBase, SillyTestClass
from qbitbridge.vqpuflow import limit_concurrent_tasks, run_tasks_with_concurrency_limit
from prefect import task, flow

cluster: str = "setonix-pypath"
vqpu_template_script: str = os.path.dirname(os.path.abspath(__file__)) + "/../qb-vqpu/vqpu_template.example.sh"
vqpu_template_yaml: str = os.path.dirname(os.path.abspath(__file__)) + "/../qb-vqpu/remote_vqpu_template.example.yaml"


@limit_concurrent_tasks(
    max_active_task=5, sleep_time_submission=10, sleep_time_active_tasks_poll=20, max_task_submissions=3
)
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
def test_flow():
    print("Test flow executed")
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
    print("Test flow results:", results)


myflow = HybridQuantumWorkflowBase(
    cluster=cluster,
    vqpu_ids=[1, 2, 3, 16],
    vqpu_template_script=vqpu_template_script,
    vqpu_template_yaml=vqpu_template_yaml,
)
task_runner = myflow.gettaskrunner("cpu")
# limitedflow = limited_flow.with_options(task_runner=task_runner)
# limitedflow()

testflow = test_flow.with_options(task_runner=task_runner)
testflow()
