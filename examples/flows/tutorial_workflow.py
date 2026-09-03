"""
@brief This example shows how to structure a prefect workflow. This also discusses some basics on what to add in the vqpu package

"""

import sys, os, re

sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/../../")
from time import sleep
import datetime
from typing import List, Set, Callable, Tuple, Dict

# import key classes from
from qbitbridge.vqpubase import (
    QPUMetaData,
    HybridQuantumWorkflowBase,
)

# import useful utilities
from qbitbridge.utils import (
    EventFile,
    save_artifact,
    get_num_gpus,
)

# import basic flows and tasks  from the vqpuflow as desired
from qbitbridge.vqpuflow import (
    # tasks
    run_cpu,
    run_gpu,
    # and here are some flows
    launch_vqpu_workflow,
    cpu_workflow,
    gpu_workflow,
    postprocessing_histo_plot,
)

#
import asyncio
from prefect import task, flow
from prefect.logging import get_run_logger
import numpy as np


# let's create some tasks
@task(name="Example task", task_run_name="example_task-{date:%Y-%m-%d:%H:%M:%S}")
def simple_task(
    date: datetime.datetime = datetime.datetime.now(),
):
    """Task"""
    pass


@task(
    name="Example async task",
    task_run_name="example_async_task-{date:%Y-%m-%d:%H:%M:%S}",
)
async def simple_async_task(
    date: datetime.datetime = datetime.datetime.now(),
):
    """Async task"""
    pass


# Now let's create some flows
@flow(
    name="Example flow",
    flow_run_name="example_flow-{date:%Y-%m-%d:%H:%M:%S}",
)
def workflow(
    myqpuworkflow: HybridQuantumWorkflowBase,
    date: datetime.datetime = datetime.datetime.now(),
) -> None:
    """Example flow"""
    logger = get_run_logger()
    logger.info("Example flow")
    # let's submit a task to the flow
    future = simple_task.submit()
    # and then get results
    future.result()
    logger.info("Finished flow")


@flow(
    name="Example async flow",
    flow_run_name="example_async_flow-{date:%Y-%m-%d:%H:%M:%S}",
)
async def async_workflow(
    myqpuworkflow: HybridQuantumWorkflowBase,
    date: datetime.datetime = datetime.datetime.now(),
) -> None:
    """Example async flow that can call asynchronous functions"""
    logger = get_run_logger()
    logger.info("Example async flow")
    # let's submit a task to the flow
    future = simple_task.submit()
    # and then get results
    future.result()
    # we can also submit several tasks at once
    futures = []
    for i in range(10):
        futures.append(simple_task.submit())
    for f in futures:
        f.result()
    # let's try submitting some asynchronous tasks
    async with asyncio.TaskGroup() as tg:
        for i in range(10):
            tg.create_task(simple_async_task.submit())
        # once the async taskgroup is finished all tasks have been submited
    # we can also just create a list of tasks
    tg = []
    for i in range(10):
        tg.append(asyncio.create_task(simple_async_task.submit()))
    done, pending = await asyncio.wait(tg)
    # once they are all done, let's get the results
    for d in done:
        d.result()

    logger.info("Finished async flow")


def wrapper_to_async_flow(
    yaml_template: str,
    script_template: str,
    cluster: str,
):
    """Run the workflow with the appropriate dask task runner constructed by the HybridQuantumWorkflowBase class.
    Args:
        yaml_template (str): filename of the yaml template related to run a vQPU
        script_template (str): filenaem of the script template used to run a vQPU
        cluster (str): cluster configuration yaml file (looks in clusters/)
    """

    myflowmanager = HybridQuantumWorkflowBase(
        cluster=cluster,
        vqpu_template_yaml=yaml_template,
        vqpu_template_script=script_template,
    )
    print(myflowmanager)

    # for none asynchronous workflow you can launch it but this will run on the local default task runner
    workflow(myqpuworkflow=myflowmanager)
    # or you can create a new flow that will a specific task runner
    newflow = workflow.with_options(task_runner=myflowmanager.gettaskrunner("cpu"))
    newflow(myqpuworkflow=myflowmanager)

    # for an asynchronous flow, call with asyncio.run in a non async function
    asyncio.run(
        async_workflow.with_options(task_runner=myflowmanager.gettaskrunner("generic"))(
            myqpuworkflow=myflowmanager
        )
    )


if __name__ == "__main__":

    import argparse
    parser = argparse.ArgumentParser(description="Start the tutorial workflow.")

    # Give a good default for development
    parser.add_argument(
        "--yaml-template",
        type=str,
        help="Path to the YAML configuration file",
        default=f"{os.path.dirname(os.path.abspath(__file__))}/../../workflow/qb-vqpu/remote_vqpu_ella_template.yaml",
    )
    parser.add_argument(
        "--script-template",
        type=str,
        help="vqpu start-up script",
        default=f"{os.path.dirname(os.path.abspath(__file__))}/../../workflow/qb-vqpu/vqpu_template_ella_qpu-1.7.0.sh",
    )
    parser.add_argument(
        "--cluster",
        type=str,
        help="Cluster configuration",
        default="ella-qb-1.7.0-pypath",
    )
    args = parser.parse_args()
    wrapper_to_async_flow(
        yaml_template=args.yaml_template,
        script_template=args.script_template,
        cluster=args.cluster,
    )
