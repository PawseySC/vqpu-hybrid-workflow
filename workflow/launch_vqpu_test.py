'''
@brief This example shows how to turn on noise in a simulation, and how to modify the default noise model used.
'''

from time import sleep
from typing import List, NamedTuple, Optional, Tuple, Union, Generator
from vqpucommon.clusters import get_dask_runners
from vqpucommon.options import vQPUWorkflow
from vqpucommon.vqpuworkflow import launch_vqpu_workflow, launch_vqpu_test_workflow, shutdown_vqpu_workflow, circuits_workflow, cpu_workflow, gpu_workflow
from vqpucommon.utils import EventFile

import asyncio
from prefect import flow, task, get_client
from prefect.states import Cancelled
from prefect.logging import get_run_logger
from prefect.artifacts import Artifact
from prefect_dask import DaskTaskRunner

     

@flow(name = "Basic vQPU Test", 
      description = "Launching and shutting down vqpu", 
      retries = 3, retry_delay_seconds = 10, 
      log_prints=True, 
      )
async def workflow(task_runners : dict, 
             arguments: str = "", ):
    '''
    @brief start and shutdown vqpu
    '''
    logger = get_run_logger()
    logger.info("Running vQPU")
    
    # create events: one for the vqpu is running, the other when circuits 
    # are finished and can shutdown vqpu
    events = {
        'vqpu_launch':EventFile(name = 'vqpu_launch', loc = './events/'), 
        'circuits_finished':EventFile(name = 'circuits_finished', loc = './events/'), 
        }

    # lets define the flows with the appropriate task runners 
    # this would be for the real vqpu 
    vqpuflow = launch_vqpu_workflow.with_options(
        task_runner = task_runners['vqpu'],
        )
    # this is for testing 
    vqputestflow = launch_vqpu_test_workflow.with_options(
        task_runner = task_runners['vqpu'],
        )
    vqpushutdownflow = shutdown_vqpu_workflow.with_options(
        task_runner = task_runners['generic']
        )

    await vqpuflow(events['vqpu_launch'], arguments)
    logger.info('Now try sleeping before shutting down')
    sleep(5)
    events['circuits_finished'].set()
    logger.info('Now try shutting down vqpu flow')
    await vqpushutdownflow(events['circuits_finished'], arguments)
    logger.info("Finished (v)QPU test flow")

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
    arguments = 'None'
    run_flow(arguments)

if __name__ == '__main__':
    cli()
