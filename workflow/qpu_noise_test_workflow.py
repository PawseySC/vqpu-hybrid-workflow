'''
@brief This example shows how to turn on noise in a simulation, and how to modify the default noise model used.
'''

from time import sleep
from typing import List, NamedTuple, Optional, Tuple, Union, Generator
from clusters.clusters import get_dask_runners, get_test_dask_runners
from common.options import vQPUWorkflow
from common.vqpuworkflow import launch_vqpu_workflow, showndown_vqpu_workflow, circuits_workflow, cpu_workflow, gpu_workflow

import asyncio
from prefect import flow, task, get_client
from prefect.states import Cancelled
from prefect.logging import get_run_logger
from prefect.artifacts import Artifact
from prefect_dask import DaskTaskRunner

     

@flow(name = "Basic vQPU Test", 
      description = "Running a (v)QPU+CPU+GPU hybrid workflow", 
      retries = 3, retry_delay_seconds = 10, 
      log_prints=True, 
      )
async def workflow(task_runners : dict, 
             arguments: str = "", ):
    '''
    @brief overall workflow for hydrid (v)QPU+CPU+GPU
    '''
    logger = get_run_logger()
    logger.info("Running hybrid (v)QPU workflow")
    
    subflows = []
    # create events: one for the vqpu is running, the other for all circuits finished
    events = {'vqpu_launch':asyncio.Event(), 'circuits_finished':asyncio.Event()}
    # launch the vqpu, this preceeds anything else 
    # but run in asynchronous fashion so don't wait for flow to finish 
    # subflows.append(
    #     asyncio.create_task(launch_vqpu_workflow.with_options(
    #     task_runner = task_runners['vqpu']
    #     )
    #     (events['vqpu_launch'], arguments)
    #     ))
    
    # now with the vqpu running with the appropriate task runners 
    # can run concurrent subflows
    # subflows.append(
    #     asyncio.create_task(circuits_workflow.with_options(
    #     task_runner = task_runners['generic'],
    #     # want to set some options for the generic task runner here.
    #     )(events['vqpu_launch'], events['circuits_finished'], arguments)
    #     ))
    subflows.append(
        asyncio.create_task(gpu_workflow.with_options(
        task_runner = task_runners['gpu'],
        # want to set some options for the generic task runner here.
        )(arguments)
        ))
    subflows.append(
        asyncio.create_task(cpu_workflow.with_options(
        task_runner = task_runners['cpu'],
        # want to set some options for the generic task runner here.
        )(arguments)
        ))
    # # then when all the circuit subflows have finished, run the clean vqpu workflow
    # # than cancels the job running the vqpu
    # shutdown_vqpu_workflow.with_options(
    #     task_runner = task_runners['generic']
    #     )(events['circuits_finished'], arguments)
    await asyncio.gather(*subflows)
    

    logger.info("Finished hybrid (v)QPU workflow")


def run_flow(arguments: str):
    '''
    @brief run the workflow with the appropriate task runner
    '''
    task_runners = get_dask_runners(cluster='ella')

    asyncio.run(workflow.with_options(
#       task_runner = task_runners['generic']
    )(task_runners, arguments))

def cli() -> None:
    import logging

    logger = logging.getLogger('vQPU')
    logger.setLevel(logging.INFO)

    # parser = get_parser()
    # args = parser.parse_args()
    arguments : str = ''
    arguments += ' --gpu-mpi-exec=/software/projects/pawsey0001/pelahi/profile_util/examples/gpu-mpi/bin/gpu-mpi-comm '
    arguments += ' --gpu-mpi-args=24.0,2 '
    arguments += ' --gpu-exec=/software/projects/pawsey0001/pelahi/profile_util/examples/gpu-openmp/bin/gpu-openmp '
    arguments += ' --gpu-args=134217728,10 '
    arguments += ' --cpu-exec=/software/projects/pawsey0001/pelahi/profile_util/examples/openmp/bin/openmpvec_cpp '
    arguments += ' --cpu-args=134217728 '

    run_flow(arguments)

if __name__ == '__main__':
    cli()
