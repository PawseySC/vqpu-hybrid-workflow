"""
@brief This example shows how to turn on noise in a simulation, and how to modify the default noise model used.
"""

import sys, os

sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/../")
from time import sleep
from typing import List, NamedTuple, Optional, Tuple, Union, Generator
from vqpucommon.clusters import get_dask_runners
from vqpucommon.options import vQPUWorkflow
from vqpucommon.vqpuworkflow_deprecated import launch_vqpu_test_workflow
from vqpucommon.utils import EventFile

import asyncio
from prefect import flow
from prefect.logging import get_run_logger


@flow(
    name="Basic vQPU Test",
    description="Launching and shutting down vqpu",
    retries=3,
    retry_delay_seconds=10,
    log_prints=True,
)
async def workflow(
    task_runners: dict,
    arguments: str = "",
):
    """
    @brief start and shutdown vqpu
    """
    logger = get_run_logger()
    logger.info("Running vQPU")

    # create events: one for the vqpu is running, the other when circuits
    # are finished and can shutdown vqpu
    events = {
        "vqpu_launch": EventFile(name="vqpu_launch", loc="./events/"),
        "circuits_finished": EventFile(name="circuits_finished", loc="./events/"),
    }

    vqputestflow = launch_vqpu_test_workflow.with_options(
        task_runner=task_runners["vqpu"],
    )
    await vqputestflow(
        launch_event=events["vqpu_launch"],
        finish_circuit_event=events["circuits_finished"],
        arguments=arguments,
        vqpu_id=2,
        walltime=20,
        circuittime=100000,
        try_real_vqpu=True,
    )
    for k in events.keys():
        events[k].clean()


def run_flow(arguments: str):
    """
    @brief run the workflow with the appropriate task runner
    """
    task_runners = get_dask_runners(cluster="ella-qb")

    asyncio.run(
        workflow.with_options(
            #       task_runner = task_runners['generic']
        )(task_runners, arguments)
    )


def cli() -> None:
    import logging

    logger = logging.getLogger("vQPU")
    logger.setLevel(logging.INFO)
    arguments = "None"
    run_flow(arguments)


if __name__ == "__main__":
    cli()
