'''
@file vqpuworkflow.py
@brief Collection of tasks and flows related to vqpu and circuits

'''

import time, datetime
import os
from typing import List, NamedTuple, Optional, Tuple, Union, Generator, Callable
from vqpucommon.utils import save_artifact, run_a_srun_process, run_a_process, get_job_info, get_flow_runs, SlurmInfo, EventFile
#from circuits.qristal_circuits import noisy_circuit
from circuits.test_circuits import test_circuit
import asyncio
from prefect import flow, task, get_client, pause_flow_run
from prefect.states import Cancelled
from prefect.logging import get_run_logger
from prefect.artifacts import Artifact
from prefect.input import RunInput
from prefect.runtime import flow_run
#from prefect.events import emit_event, DeploymentEventTrigger
from prefect.client.schemas.filters import FlowRunFilter, FlowFilter


async def create_vqpu_remote_yaml(job_info : Union[SlurmInfo], 
                            vqpu_id : int = 1, 
                            template_fname : str = 'remote_vqpu_template.yaml'
                            ):
    '''
    function that saves the remote backend for the vqpu to an artifcat 
    '''
    workflow_yaml = f'vqpus/remote_vqpu-{vqpu_id}.yaml'
    template_path = os.path.dirname(os.path.abspath(__file__))
    fname = template_path + '/' + template_fname
    lines = open(fname, 'r').readlines()
    fout = open(workflow_yaml, 'w')
    for line in lines:
        if 'HOSTNAME' in line:
            line.replace('HOSTNAME', job_info.hostname)
        fout.write(line)
    # to store the results of this task, make use of a helper function that creates artifcat 
    await save_artifact(workflow_yaml, key=f'remote{vqpu_id}')
    await save_artifact(job_info.job_id, key=f'vqpujobid{vqpu_id}')

@task(retries = 5, 
      retry_delay_seconds = 10, 
      timeout_seconds=600
      )
async def launch_vqpu(event: EventFile, 
                      arguments: str = '', 
                      vqpu_id : int = 1,
                      ):
    '''
    @brief base task that launches the virtual qpu. Should have minimal retries and a wait between retries
    once vqpu is launched set event so subsequent circuit tasks can run 
    '''
    logger = get_run_logger()
    logger.info(f'Spinning up vQPU-{vqpu_id} backend')
    job_info = get_job_info()
    await create_vqpu_remote_yaml(job_info, vqpu_id=vqpu_id)
    curpath = os.path.dirname(os.path.abspath(__file__))
    cmds = [f'{curpath}/vqpu.sh', '&']
    process = run_a_process(cmds)
    await asyncio.sleep(5)
    event.set()
    flow_id = flow.flow_run.id
    await save_artifact(f'vqpuflow.vqpu-{vqpu_id}.yaml', key=f'flow-{flow_id}.vqpu-{vqpu_id}')
    logger.info(f'Flow {flow_id} running vQPU-{vqpu_id} ... ')

@task(retries = 5, 
      retry_delay_seconds = 10, 
      timeout_seconds=600
      )
async def run_vqpu(vqpu_id : int = 1,
                   walltime : float = 86400, # one day
                      ):
    '''
    @brief runs a task that sleeps to keep flow active  
    '''
    logger = get_run_logger()
    logger.info(f'vQPU-{vqpu_id} running ..')
    await asyncio.sleep(walltime*0.99)
    logger.info(f'vQPU-{vqpu_id} wrapping up due to slurm walltime limit ... ')

@task(retries = 5, 
      retry_delay_seconds = 10, 
      timeout_seconds=600
      )
async def launch_vqpu_test(event: EventFile, 
                      arguments: str = '', 
                      vqpu_id : int = 1,
                      ):
    '''
    @brief base task that launches the virtual qpu. Should have minimal retries and a wait between retries
    once vqpu is launched set event so subsequent circuit tasks can run 
    '''
    logger = get_run_logger()
    logger.info(f'Spinning up Test vQPU-{vqpu_id} backend')
    job_info = get_job_info()
    create_vqpu_remote_yaml(job_info, vqpu_id=vqpu_id)
    cmds = ['echo', 'justtesting']
    process = run_a_process(cmds)
    await asyncio.sleep(5)
    event.set()
    logger.info(f'Test vQPU-{vqpu_id} running ... ')

@task(retries = 5, 
      retry_delay_seconds = 2, 
      timeout_seconds=600
      )
async def shutdown_vqpu(arguments: str, 
                  vqpu_id : int = 1,
                  vqpu_flow_name : str = 'launch_vqpu_flow'
                  ):
    '''
    @brief base task that shuts down the virtual qpu. Should have minimal retries and a wait between retries 
    '''
    logger = get_run_logger()
    logger.info(f'Shutdown vQPU-{vqpu_id} backend')
    artifact = await Artifact.get(key=f'vqpujobid{vqpu_id}')
    jobid = dict(artifact)['data'].split('\n')[1]
    # want to cancel the vQPU flow
    # cancel all flows that match a specific name 
    # eventually will need to check that the jobid is 
    # the correct one. 
    flow_run_filter = FlowRunFilter(
        # state = {"equals_": "Running"},
        name = {"contains_": vqpu_flow_name},
        )
    runs = await get_flow_runs(flow_run_filter=flow_run_filter)
    for run in runs:
        logger.info(f"Cancelling vQPU associated with flow \
                    {run.name} with id {run.id}\
                         {run.state}")
        # await client.set_flow_run_state(
        #     flow_run_id = flow.id, state = "Cancelled", force = True
        # )
        # flow.set_flow_run_state(
        #     state = Cancelled(message="Shutdown vQPU running from run ")
        # )

    # async with get_client() as client:

    #     # Retrieve running flow runs
    #     #flows = await client.get_current_flow_runs(flow_run_filter=flow_run_filter})
    #     flows = await client.read_flow_runs(flow_run_filter = flow_run_filter)
    #     # Find the flows to cancel it
    #     for flow in flows:
    #         logger.info(f"Cancelling vQPU associated with flow \
    #                     {flow.name} with id {flow.id}\
    #                         ")
    #         # await client.set_flow_run_state(
    #         #     flow_run_id = flow.id, state = "Cancelled", force = True
    #         # )
    #         # flow.set_flow_run_state(
    #         #     state = Cancelled(message="Shutdown vQPU running from run ")
    #         # )
    logger.info("vQPU(s) shutdown")

@task(retries = 10, 
      retry_delay_seconds = 2,
      retry_jitter_factor=0.5
      )
async def run_circuit(arguments : str,
                      circuitfunc : Callable, 
                      vqpu_id : int = 1,
                      verbose : bool = 'True'
                      ):
    '''
    @brief run a simple circuit on a given remote backend
    '''
    # note that below since artifcate saves remote file name, don't need the below check and parsing
    artifact = await Artifact.get(key=f'remote{vqpu_id}')
    remote = dict(artifact)['data'].split('\n')[1]
    if verbose:
        print(remote)
    results = circuitfunc(remote, arguments)
    return results

# @run_circuit.on_rollback
# def rollback_circuit(transaction):
#     """ currently empty role back """
#     sleep(2)


@flow(name = "launch_vqpu_flow", 
      flow_run_name = "launch_vqpu_{vqpu_id}_flow_on-{date:%Y-%m-%d:%H:%M:%S}",
      description = "Launching the vQPU only portion with the appropriate task runner", 
      retries = 3, retry_delay_seconds = 10, 
      log_prints=True, 
      )
async def launch_vqpu_workflow(event : EventFile,
                                    vqpu_id : int = 1,
                                    walltime : float = 10, 
                                    arguments : str = "", 
                                    date : datetime.datetime = datetime.datetime.now() 
                                    ):
    '''
    @brief vqpu workflow that should be invoked with the appropriate task runner
    '''
    future = await launch_vqpu.submit(event, vqpu_id = vqpu_id, arguments = arguments)
    await future.result()
    future = await run_vqpu.submit(walltime = walltime)
    await future.result()

@flow(name = "launch_vqpu_test_flow", 
      flow_run_name = "launch_vqpu_test_{vqpu_id}_flow_on-{date:%Y-%m-%d:%H:%M:%S}",
      description = "Launching the vQPU only portion with the appropriate task runner", 
      retries = 3, retry_delay_seconds = 10, 
      log_prints=True, 
      )
async def launch_vqpu_test_workflow(event : EventFile,
                                    vqpu_id : int = 1,
                                    walltime : float = 10, 
                                    arguments : str = "", 
                                    date : datetime.datetime = datetime.datetime.now() 
                                    ):
    '''
    @brief vqpu workflow that should be invoked with the appropriate task runner
    '''
    future = await launch_vqpu_test.submit(event, vqpu_id = vqpu_id, arguments = arguments)
    await future.result()
    future = await run_vqpu.submit(walltime = walltime)
    await future.result()

@flow(name = "shutdown_vqpu_flow", 
      flow_run_name = "showndown_vqpu_flow-on-{date:%Y-%m-%d:%H:%M:%S}",
      description = "Shutdown the vQPU", 
      retries = 3, retry_delay_seconds = 10, 
      log_prints=True,
      )
async def shutdown_vqpu_workflow(event : EventFile,
                                 vqpu_id : int = 1,  
                                 arguments : str = "", 
                                 date : datetime.datetime = datetime.datetime.now()
                                ):
    '''
    @brief vqpu workflow that should be invoked with the appropriate task runner. Mean to shutdown the vqpu
    '''
    # wait till the shutdown event has been set so that all circuits have been run
    await event.wait()
    event.clean()
    future = await shutdown_vqpu.submit(arguments = arguments, vqpu_id = vqpu_id)
    await future.result()

@flow(name = "Circuits flow", 
      description = "Running circutis on the vQPU with the appropriate task runner for launching circuits", 
      retries = 3, retry_delay_seconds = 10, 
      log_prints = True, 
      )
async def circuits_workflow(vqpu_event : EventFile, 
                            circuit_event: EventFile, 
                            arguments : str = ""):
    '''
    @brief vqpu workflow that should be invoked with the appropriate task runner. Mean to launch the vqpu
    '''
    await vqpu_event.wait()
    future = await run_circuit.submit(arguments, test_circuit)
    results = await future.result()
    circuit_event.set()
    return results

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
    logger.info(cmds)
    process = run_a_process(cmds)
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
    logger.info(cmds)
    process = run_a_process(cmds)
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
    logger = get_run_logger()
    logger.info("Launching CPU flow")
    # submit the task and wait for results 
    future = await run_cpu.submit(arguments)
    await future.result()
    logger.info("Finished CPU flow")

@flow(name = "gpu flow", 
      description = "Running gpu flows", 
      retries = 3, retry_delay_seconds = 10, 
      log_prints=True, 
      )
async def gpu_workflow(arguments : str = ""):
    '''
    @brief gpu workflow that should be invoked with the appropriate task runner
    '''
    logger = get_run_logger()
    logger.info("Launching GPU flow")
    # submit the task and wait for results 
    future = await run_gpu.submit(arguments)
    await future.result()
    logger.info('Finished GPU flow')
    
    # run_gpu(arguments)

