'''
@brief This example shows how to turn on noise in a simulation, and how to modify the default noise model used.
'''

from time import sleep
from typing import List, NamedTuple, Optional, Tuple, Union, Generator
from clusters.clusters import get_dask_runners
from common.utils import SlurmInfo, get_environment_variable, get_job_info, log_slurm_job_environment, run_a_process, save_artifact
import common.options 

import asyncio
from prefect import flow, task, get_client
from prefect.states import Cancelled
from prefect.logging import get_run_logger
from prefect.artifacts import Artifact

@task(retries = 10, 
      retry_delay_seconds = 2,
      timeout_seconds=3600,
      result_serializer="compressed/json"
      )
async def run_cpu(arguments: str):
    '''
    @brief simple cpu kernel with persistent results 
    '''
    logger = get_run_logger()
    logger.info("Launching CPU task")
    
    cmds = arguments.split('--cpu-exec=')[1].split(' ')[0].split(',')
    cmds += arguments.split('--cpu-args=')[1].split(' ')[0].split(',')
    process = run_a_process(cmds, logger)
    logger.info("Finished CPU task")

@task(retries = 10, 
      retry_delay_seconds = 2,
      timeout_seconds=3600,
      result_serializer="compressed/json"
      )
async def run_gpu(arguments: str):
    '''
    @brief simple gpu kernel with persistent results
    '''
    
    logger = get_run_logger()
    logger.info("Launching GPU task")
    cmds = arguments.split('--gpu-exec=')[1].split(' ')[0].split(',')
    cmds += arguments.split('--gpu-args=')[1].split(' ')[0].split(',')
    process = run_a_process(cmds, logger)
    logger.info("Finished GPU task")


def create_vqpu_remote_yaml(job_info: 
                            Union[SlurmInfo], 
                            template_path : str = 'clusters/remote_vqpu_template.yaml'):
    workflow_yaml = 'remote_vqpu.yaml'
    cmds = [f'sed :s:HOSTNAME:{job_info.hostname}:g {template_path} > {workflow_yaml}']
    process = run_a_process(cmds)
    # to store the results of this task, make use of a helper function that creates artifcat 
    save_artifact(workflow_yaml, key='remote')
    save_artifact(job_info.job_id, key='vqpuid')

@task(retries = 5, 
      retry_delay_seconds = 10, 
      timeout_seconds=600
      )
async def launch_vqpu(event: asyncio.Event, arguments: str):
    '''
    @brief base task that launches the qpu. Should have minimal retries and a wait between retries
    once vqpu is launched set event so subsequent circuit tasks can run 
    '''
    logger = get_run_logger()
    logger.info("Spinning up vQPU backend")
    job_info = get_job_info()
    create_vqpu_remote_yaml(job_info)
    cmds = ['vqpu.sh']
    process = run_a_process(cmds, logger)
    event.set()
    logger.info("vQPU running ... ")


@task(retries = 5, 
      retry_delay_seconds = 2, 
      timeout_seconds=600
      )
def shutdown_vqpu(arguments: str):
    '''
    @brief base task that launches the qpu. Should have minimal retries and a wait between retries 
    '''
    logger = get_run_logger()
    logger.info("Shutdown vQPU backend")
    jobid = Artifact.get(key="vqpuid").data
    # want to cancel the vQPU flow
    # cancel all flows that match a specific name 
    with get_client() as client:
        
        flows = client.get_current_flow_runs(
            flow_run_filter={"name": "launch_vqpu_flow"}
        )
        # Find the flows to cancel it
        for flow in flows:
            flow.set_flow_run_state(
                state=Cancelled(message="Shutdown vQPU running from run ")
            )
    logger.info("vQPU(s) shutdown")

@task(retries = 10, 
      retry_delay_seconds = 2,
      retry_jitter_factor=0.5
      )
async def run_circuit(arguments: str):
    '''
    @brief run a simple circuit on a given remote backend
    '''
    # note that below since artifcate saves remote file name, don't need the below check and parsing
    remote = Artifact.get(key="remote").data
    print(remote)
    results = None
    #results = noisy_circuit(remote, arguments)
    return results

@run_circuit.on_rollback
def rollback_circuit(transaction):
    """ currently empty role back """
    sleep(2)


@flow(name = "launch_vqpu_flow", 
      description = "Launching the vQPU only portion with the appropriate task runner", 
      retries = 3, retry_delay_seconds = 10, 
      log_prints=True, 
      )
async def vqpu_workflow(event : asyncio.Event, arguments : str = ""):
    '''
    @brief vqpu workflow that should be invoked with the appropriate task runner
    '''
    launch_vqpu(event, arguments)

@flow(name = "shutdown_vqpu_flow", 
      description = "Shutdown the vQPU", 
      retries = 3, retry_delay_seconds = 10, 
      log_prints=True, 
      )
def vqpu_shutdown_workflow(arguments : str = ""):
    '''
    @brief vqpu workflow that should be invoked with the appropriate task runner. Mean to launch the vqpu
    '''
    shutdown_vqpu(arguments)

@flow(name = "Circuits flow", 
      description = "Running circutis on the vQPU with the appropriate task runner for launching circuits", 
      retries = 3, retry_delay_seconds = 10, 
      log_prints=True, 
      )
async def circuits_workflow(arguments : str = ""):
    '''
    @brief vqpu workflow that should be invoked with the appropriate task runner. Mean to launch the vqpu
    '''
    results = run_circuit(arguments)
    print(results)
    await asyncio.sleep(1)

@flow(name = "cpu flow", 
      description = "Running cpu flows", 
      retries = 3, retry_delay_seconds = 10, 
      log_prints=True, 
      )
async def cpu_workflow(arguments : str = ""):
    '''
    @brief cpu workflow that should be invoked with the appropriate task runner
    '''
    run_cpu(arguments)
    await asyncio.sleep(1)

@flow(name = "gpu flow", 
      description = "Running gpu flows", 
      retries = 3, retry_delay_seconds = 10, 
      log_prints=True, 
      )
async def gpu_workflow(arguments : str = ""):
    '''
    @brief gpu workflow that should be invoked with the appropriate task runner
    '''
    await run_gpu(arguments)
     

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
    '''
    # create an event that needs to be set once the vqpu is running 
    vqpu_event = asyncio.Event()
    # launch the vqpu, this preceeds anything else 
    # but run in asynchronous fashion so don't wait for flow to finish 
    asyncio.create_task(vqpu_workflow.with_options(
        task_runner = task_runners['vqpu']
        )(vqpu_event, arguments))
    
    # now with the vqpu running with the appropriate task runners 
    # can run concurrent subflows
    subflows.append(
        circuits_workflow.with_options(
        task_runner = task_runners['generic'],
        # want to set some options for the generic task runner here.
        )(vqpu_event, arguments)
        )
    subflows.append(
        gpu_workflow.with_options(
        task_runner = task_runners['gpu'],
        # want to set some options for the generic task runner here.
        )(arguments)
        )
    subflows.append(
        cpu_workflow.with_options(
        task_runner = task_runners['cpu'],
        # want to set some options for the generic task runner here.
        )(arguments)
        )
    await asyncio.gather(*subflows)
    # # then when all the subflows have finished, run the clean vqpu workflow
    # # than cancels the job running the vqpu
    # vqpu_shutdown_workflow.with_options(
    #     task_runner = task_runners['generic']
    #     )(arguments)
    '''

    logger.info("Finished hybrid (v)QPU workflow", results)


def run_flow(arguments: str):
    '''
    @brief run the workflow with the appropriate task runner
    '''
    task_runners = get_dask_runners(cluster='ella')

    asyncio.run(workflow.with_options(
        task_runner = task_runners['generic']
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