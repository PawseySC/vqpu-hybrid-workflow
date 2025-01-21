'''
@file vqpuworkflow.py
@brief Collection of tasks and flows related to vqpu and circuits

'''

import time, datetime, subprocess, os, select
from typing import List, NamedTuple, Optional, Tuple, Union, Generator, Callable
from vqpucommon.utils import save_artifact, run_a_srun_process, run_a_process, run_a_process_bg, get_job_info, get_flow_runs, SlurmInfo, EventFile
#from circuits.qristal_circuits import noisy_circuit
from circuits.test_circuits import test_circuit
import asyncio
from prefect import flow, task, get_client, pause_flow_run
from prefect.context import FlowRunContext
from prefect.states import Cancelled
from prefect.logging import get_run_logger
from prefect.artifacts import Artifact
from prefect.input import RunInput
from prefect.runtime import flow_run
#from prefect.events import emit_event, DeploymentEventTrigger
from prefect.client.schemas.filters import FlowRunFilter, FlowFilter
from prefect.utilities.timeout import timeout_async, timeout


async def create_vqpu_remote_yaml(job_info : Union[SlurmInfo], 
                            # flow_id : int,  
                            vqpu_id : int , 
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
    # I was thinking the best way of shutting down a task is to cancel the subflow ... 
    # but I think I will change my approach 
    #await save_artifact(f'vqpuflow.vqpu-{vqpu_id}.yaml', key=f'flow-{flow_id}.vqpu-{vqpu_id}')

@task(retries = 5, 
      retry_delay_seconds = 10, 
      timeout_seconds=600,
      name = 'Task-Launch-vQPU',
      )
async def launch_vqpu(event: EventFile, 
                      vqpu_id : int,
                      arguments : str = '', 
                      path_to_vqpu_script : str = os.path.dirname(os.path.abspath(__file__))+'/../qb-vqpu/', 
                      spinuptime : float = 20, 
                      ):
    '''
    @brief base task that launches the virtual qpu. Should have minimal retries and a wait between retries
    once vqpu is launched set event so subsequent circuit tasks can run 
    '''
    # get flow run information and slurm job information 
    job_info = get_job_info()
    script = f'{path_to_vqpu_script}/vqpu.sh'
    logger = get_run_logger()
    logger.info(f'Spinning up vQPU-{vqpu_id} backend')
    logger.info(f'vQPU-{vqpu_id}: setting config yamls')
    await create_vqpu_remote_yaml(job_info, vqpu_id=vqpu_id)
    logger.info(f'vQPU-{vqpu_id}: running script {script}')
    cmds = [script]
    process = run_a_process(cmds)
    await asyncio.sleep(spinuptime)
    logger.info(f'vQPU-{vqpu_id}: script running ... ')
    # read the output of the process in a non-blocking manner 
    event.set()
    logger.info(f'Running vQPU-{vqpu_id} ... ')

@task(retries = 5, 
      retry_delay_seconds = 10, 
      timeout_seconds=600
      )
async def launch_vqpu_test(
    event: EventFile, 
    vqpu_id : int,
    arguments: str = '', 
    ):
    '''
    @brief base task that launches the virtual qpu. Should have minimal retries and a wait between retries
    once vqpu is launched set event so subsequent circuit tasks can run 
    '''
    job_info = get_job_info()
    logger = get_run_logger()
    logger.info(f'Spinning up Test vQPU-{vqpu_id} backend')
    await create_vqpu_remote_yaml(job_info, vqpu_id=vqpu_id)
    cmds = ['echo', 'justtesting']
    process = run_a_process(cmds)
    await asyncio.sleep(5)
    event.set()
    logger.info(f'Test vQPU-{vqpu_id} running ... ')

async def task_circuitcomplete(event : EventFile, vqpu_id : int) -> str:
    '''
    @brief async task that indicates why vqpu can proceed to be shutdown as all circuits have been run
    '''
    await event.wait()
    return "Circuits completed!"

async def task_testcircuitcomplete(event : EventFile, circuittime : float, vqpu_id : int) -> str:
    '''
    @briefasync task that indicates why vqpu can proceed to be shutdown as all the circuits have been run mimicked by a time to wait
    '''
    await asyncio.sleep(circuittime)
    event.set()
    return "Circuits completed!"

async def task_walltime(walltime : float, vqpu_id : int) -> str:
    '''
    @brief async task that indicates why vqpu can proceed to be shutdown as has exceeded walltime
    '''
    await asyncio.sleep(walltime)
    return 'Walltime complete'

@task(retries = 0, 
      )
async def run_vqpu(
    event : EventFile,
    vqpu_id : int,
    walltime : float = 86400, # one day
    ):
    '''
    @brief runs a task that sleeps to keep flow active  
    '''
    logger = get_run_logger()
    logger.info(f'vQPU-{vqpu_id} running and will keep running till circuits complete or hit walltime ... ')
    # generate a list of asyncio tasks to determine when to proceed and shutdown the vqpu
    tasks = [
        asyncio.create_task(task_circuitcomplete(event, vqpu_id=vqpu_id)),
        asyncio.create_task(task_walltime(walltime, vqpu_id=vqpu_id)),
    ]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    results = f"vQPU-{vqpu_id} can be shutdown. Reason : "
    for d in done:
        results += d.result()
    for remaining in pending:
        remaining.cancel()
    logger.info(results)

@task(retries = 0, 
      )
async def run_vqpu_test(
    event : EventFile,
    vqpu_id : int,
    walltime : float = 86400, # one day
    circuittime : float = -1, 
    ):
    '''
    @brief runs a task that sleeps to keep flow active  
    '''
    logger = get_run_logger()
    logger.info(f'vQPU-{vqpu_id} running and will keep running till circuits complete or hit walltime ... ')    
    tasks = [
        asyncio.create_task(task_testcircuitcomplete(event, circuittime=circuittime, vqpu_id=vqpu_id)),
        asyncio.create_task(task_walltime(walltime, vqpu_id=vqpu_id)),
    ]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    results = f"vQPU-{vqpu_id} can be shutdown. Reason :"
    for d in done:
        results += d.result()
    for remaining in pending:
        remaining.cancel()
    logger.info(results)

@task(retries = 5, 
      retry_delay_seconds = 2, 
      timeout_seconds=600
      )
async def shutdown_vqpu(arguments: str, 
                  vqpu_id : int,
                  ):
    '''
    @brief base task that shuts down the virtual qpu. Should have minimal retries and a wait between retries 
    '''
    logger = get_run_logger()
    logger.info(f'Shutdown vQPU-{vqpu_id} backend')
    # add any commands that should be passed to the qcstack script by running another script 
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
async def launch_vqpu_workflow(
    launch_event : EventFile, 
    finish_circuit_event : EventFile, 
    vqpu_id : int = 1,
    walltime : float = 86400, 
    arguments : str = "", 
    date : datetime.datetime = datetime.datetime.now() 
    ):
    '''
    @brief vqpu workflow that should be invoked with the appropriate task runner
    '''
    future = await launch_vqpu.submit(launch_event, vqpu_id = vqpu_id, arguments = arguments)
    await future.result()
    logger = get_run_logger()
    logger.info(f'vQPU-{vqpu_id} running and will keep running till circuits complete or hit walltime ... ')
    future = await run_vqpu.submit(finish_circuit_event, vqpu_id = vqpu_id, walltime = walltime)
    await future.result()
    future = await shutdown_vqpu.submit(arguments = arguments, vqpu_id = vqpu_id)

@flow(name = "launch_vqpu_test_flow", 
      flow_run_name = "launch_vqpu_test_{vqpu_id}_flow_on-{date:%Y-%m-%d:%H:%M:%S}",
      description = "Launching the vQPU only portion with the appropriate task runner", 
      retries = 3, retry_delay_seconds = 10, 
      log_prints=True, 
      )
async def launch_vqpu_test_workflow(
    launch_event : EventFile, 
    finish_circuit_event : EventFile, 
    vqpu_id : int = 1,
    walltime : float = 86400,
    circuittime : float = -1, 
    try_real_vqpu : bool = False, 
    arguments : str = "", 
    date : datetime.datetime = datetime.datetime.now() 
    ):
    '''
    @brief vqpu workflow that should be invoked with the appropriate task runner
    This spins up the vqpu and then waits for either all the circuits to have been run 
    or the walltime to have passed. 
    '''
    # 
    logger = get_run_logger()
    logger.info('Running vqpu launch and shutdown workflow test (no actual vQPU invoked)')
    if try_real_vqpu: 
        future = await launch_vqpu.submit(launch_event, vqpu_id = vqpu_id, arguments = arguments)
    else:
        future = await launch_vqpu_test.submit(launch_event, vqpu_id = vqpu_id, arguments = arguments)
    await future.result()
    future = await run_vqpu_test.submit(finish_circuit_event, vqpu_id = vqpu_id, walltime = walltime, circuittime = circuittime)
    await future.result()
    future = await shutdown_vqpu.submit(arguments = arguments, vqpu_id = vqpu_id)

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

