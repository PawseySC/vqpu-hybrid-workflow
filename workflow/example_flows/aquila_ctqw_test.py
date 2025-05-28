"""
@brief This example shows how to structure a multi-vqpu workflow.

This workflow spins up two or more vqpus and then has a workflow that runs cpu/gpu flows that also then spawn circuit flows.

"""

import sys, os

sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/../")
from time import sleep
import datetime
from typing import List, Set, Callable, Tuple, Dict
from vqpucommon.options import vQPUWorkflow
from vqpucommon.vqpubase import HybridQuantumWorkflowBase
from vqpucommon.vqpuflow import (
    launch_vqpu_workflow,
    circuits_with_nqvpuqs_workflow,
    circuits_vqpu_workflow,
    cpu_workflow,
    gpu_workflow,
    postprocessing_histo_plot,
    run_cpu,
    run_circuits_once_vqpu_ready,
)
from vqpucommon.utils import EventFile, save_artifact
from circuits.qristal_circuits import simulator_setup, noisy_circuit
import asyncio
from prefect import flow
from prefect_dask import DaskTaskRunner
from prefect.logging import get_run_logger
import numpy as np


@task()
def ctqw_config(config: str) -> Dict:
    args = dict()
    return args


@flow(
    name="Aquila CTQW grid search",
    flow_run_name="ctqw_grid_search-{date:%Y-%m-%d:%H:%M:%S}",
    description="Grid search for Continuous Time Quantum Walks on Aquila Analog Quantum Computer",
    retries=3,
    retry_delay_seconds=10,
    log_prints=True,
)
async def aquila_grid_search(
    myqpuworkflow: HybridQuantumWorkflowBase,
    config: str,
    date: datetime.datetime = datetime.datetime.now(),
) -> None:
    """Flow for running cpu based programs.

    arguments (str): string of arguments to pass extra options to run cpu
    """
    logger = get_run_logger()
    logger.info("Launching Aquila CTQW Grid Search")
    logger.info("Finished Search")


def wrapper_to_async_flow(config: str):
    """
    @brief run the workflow with the appropriate task runner
    """
    myflow = HybridQuantumWorkflowBase(
        cluster="ella-qb",
        vqpu_ids=[1, 2, 3, 16],
    )
    workflow = aquila_grid_search.with_options(task_runner=myflow.gettaskrunner("cpu"))
    asyncio.run(workflow(myqpuworkflow=myflow, config=config))


def cli(
    local_run: bool = False,
) -> None:
    import logging

    logger = logging.getLogger("vQPU")
    logger.setLevel(logging.INFO)
    wrapper_to_async_flow()


if __name__ == "__main__":
    wrapper_to_async_flow(config="some_config.yaml")
