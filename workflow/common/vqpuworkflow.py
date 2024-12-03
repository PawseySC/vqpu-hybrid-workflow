'''
@file vqpuworkflow.py
@brief Collection of tasks and flows related to vqpu and circuits

'''

import datetime
import os
from typing import List, NamedTuple, Optional, Tuple, Union, Generator, Callable
from common.utils import save_artifact, run_a_process, SlurmInfo
#from circuits.qristal_circuits import noisy_circuit
from circuits.test_circuits import test_circuit
import asyncio
from prefect import flow, task, get_client, pause_flow_run
from prefect.states import Cancelled
from prefect.logging import get_run_logger
from prefect.artifacts import Artifact
from prefect.input import RunInput
from prefect.events import emit_event, DeploymentEventTrigger




def create_vqpu_remote_yaml(job_info : Union[SlurmInfo], 
                            vqpu_id : str = '1', 
                            template_path : str = 'clusters/remote_vqpu_template.yaml'
                            ):
    '''
    function that saves the remote backend for the vqpu to an artifcat 
    '''
    workflow_yaml = f'remote_vqpu-{vqpu_id}.yaml'
    cmds = [f'sed :s:HOSTNAME:{job_info.hostname}:g {template_path} > {workflow_yaml}']
    process = run_a_process(cmds)
    # to store the results of this task, make use of a helper function that creates artifcat 
    save_artifact(workflow_yaml, key=f'remote{vqpu_id}')
    save_artifact(job_info.job_id, key=f'vqpujobid{vqpu_id}')

@task(retries = 5, 
      retry_delay_seconds = 10, 
      timeout_seconds=600
      )
async def launch_vqpu(event: asyncio.Event, 
                      arguments: str, 
                      vqpu_id : str = '1',
                      ):
    '''
    @brief base task that launches the virtual qpu. Should have minimal retries and a wait between retries
    once vqpu is launched set event so subsequent circuit tasks can run 
    '''
    logger = get_run_logger()
    logger.info('Spinning up vQPU-{vqpu_id} backend')
    job_info = get_job_info()
    create_vqpu_remote_yaml(job_info, vqpu_id=vqpu_id)
    cmds = ['vqpu.sh']
    process = run_a_process(cmds, logger)
    #event.set()
    emit_event("launched_vqpu", resource={"name": "vqpu_launched"})
    logger.info(f'vQPU-{vqpu_id} running ... ')


@task(retries = 5, 
      retry_delay_seconds = 2, 
      timeout_seconds=600
      )
def shutdown_vqpu(arguments: str, 
                  vqpu_id : str = '1',
                  ):
    '''
    @brief base task that shuts down the virtual qpu. Should have minimal retries and a wait between retries 
    '''
    logger = get_run_logger()
    logger.info(f'Shutdown vQPU-{vqpu_id} backend')
    jobid = Artifact.get(key=f'vqpujobid{vqpu_id}').data
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
async def run_circuit(arguments : str,
                      circuitfunc : Callable, 
                      vqpu_id : str = '1',
                      verbose : bool = 'True'
                      ):
    '''
    @brief run a simple circuit on a given remote backend
    '''
    # note that below since artifcate saves remote file name, don't need the below check and parsing
    remote = Artifact.get(key=f'remote{vqpu_id}').data
    if verbose:
        print(remote)
    results = circuitfunc(remote, arguments)
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
async def launch_vqpu_workflow(event : asyncio.Event, arguments : str = ""):
    '''
    @brief vqpu workflow that should be invoked with the appropriate task runner
    '''
    launch_vqpu(event, arguments)

@flow(name = "shutdown_vqpu_flow", 
      description = "Shutdown the vQPU", 
      retries = 3, retry_delay_seconds = 10, 
      log_prints=True, 
      )
def showndown_vqpu_workflow(arguments : str = ""):
    '''
    @brief vqpu workflow that should be invoked with the appropriate task runner. Mean to launch the vqpu
    '''
    shutdown_vqpu(arguments)

@flow(name = "Circuits flow", 
      description = "Running circutis on the vQPU with the appropriate task runner for launching circuits", 
      retries = 3, retry_delay_seconds = 10, 
      log_prints = True, 
      )
async def circuits_workflow(event : asyncio.Event, arguments : str = ""):
    '''
    @brief vqpu workflow that should be invoked with the appropriate task runner. Mean to launch the vqpu
    '''
    await event.wait()
    results = run_circuit(arguments, test_circuit)
    print(results)
    await asyncio.sleep(1)

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

@flow(name = "cpu flow", 
      description = "Running cpu flows", 
      retries = 3, retry_delay_seconds = 10, 
      log_prints=True, 
      )
async def cpu_workflow(arguments : str = ""):
    '''
    @brief cpu workflow that should be invoked with the appropriate task runner
    '''
    await run_cpu(arguments)

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

