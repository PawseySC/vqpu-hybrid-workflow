'''
@file vqpuworkflow.py
@brief Collection of tasks and flows related to vqpu and circuits

'''

import time, datetime, subprocess, os, select, psutil
import numpy as np
from typing import List, Any, Dict, NamedTuple, Optional, Tuple, Union, Generator, Callable
from vqpucommon.utils import save_artifact, run_a_srun_process, run_a_process, run_a_process_bg, get_job_info, get_flow_runs, SlurmInfo, EventFile
#from circuits.qristal_circuits import noisy_circuit
#from circuits.test_circuits import test_circuit
import asyncio
from prefect import flow, task, get_client, pause_flow_run
# from prefect.context import FlowRunContext
# from prefect.states import Cancelled
from prefect.logging import get_run_logger
from prefect.artifacts import Artifact
# from prefect.input import RunInput
# from prefect.runtime import flow_run
#from prefect.events import emit_event, DeploymentEventTrigger
# from prefect.client.schemas.filters import FlowRunFilter, FlowFilter
# from prefect.utilities.timeout import timeout_async, timeout

def test_circuit(remote : str, arguments : str):
    results = np.ones([2,3])
    return results

async def create_vqpu_remote_yaml(
    job_info : Union[SlurmInfo], 
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
            line = line.replace('HOSTNAME', job_info.hostname)
        fout.write(line)
    # to store the results of this task, make use of a helper function that creates artifcat 
    await save_artifact(workflow_yaml, key=f'remote{vqpu_id}')
    await save_artifact(job_info.job_id, key=f'vqpujobid{vqpu_id}')

def delete_vqpu_remote_yaml(
    vqpu_id : int, 
    ):
    workflow_yaml = f'vqpus/remote_vqpu-{vqpu_id}.yaml'
    if os.path.isfile(workflow_yaml):
        os.remove(workflow_yaml)

# def failed_to_launch():
#     delete_vqpu_remote_yaml()

@task(retries = 5, 
      retry_delay_seconds = 10, 
      timeout_seconds=600,
      name = 'Task-Launch-vQPU',
      )
async def launch_vqpu(
    event: EventFile, 
    vqpu_id : int,
    arguments : str = '', 
    path_to_vqpu_script : str = os.path.dirname(os.path.abspath(__file__))+'/../qb-vqpu/', 
    spinuptime : float = 30, 
    ) -> None:
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
    ) -> None:
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
    ) -> None:
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
    ) -> None:
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
async def shutdown_vqpu(
    arguments: str, 
    vqpu_id : int,
    ) -> None:
    '''
    @brief base task that shuts down the virtual qpu. Should have minimal retries and a wait between retries 
    '''
    logger = get_run_logger()
    logger.info(f'Shutdown vQPU-{vqpu_id} backend')
    delete_vqpu_remote_yaml(vqpu_id)
    # add any commands that should be passed to close the vqpu service
    if '--vqpu-exec=' in arguments:
        procname = arguments.split('--vqpu-exec=')[1].split(' ')[0].split(',')
        for proc in psutil.process_iter():
            # check whether the process name matches
            if proc.name() == procname:
                proc.kill()
    logger.info("vQPU(s) shutdown")

@task(retries = 10, 
      retry_delay_seconds = 2,
      retry_jitter_factor=0.5
      )
async def run_circuit(
    circuitfunc : Callable, 
    vqpu_id : int = 1,
    arguments : str = '',
    remote : str = '', 
    ) -> None:
    '''
    @brief run a simple circuit on a given remote backend
    '''
    # note that below since artifcate saves remote file name, don't need the below check and parsing
    results = circuitfunc(remote, arguments)
    return results

# @run_circuit.on_rollback
# def rollback_circuit(transaction):
#     """ currently empty role back """
#     sleep(2)


@flow(name = "launch_vqpu_flow", 
      flow_run_name = "launch_vqpu_{vqpu_id}_flow_on-{date:%Y-%m-%d:%H:%M:%S}",
      description = "Launching the vQPU only portion with the appropriate task runner", 
      retries = 3, 
      retry_delay_seconds = 10, 
      log_prints=True, 
      )
async def launch_vqpu_workflow(
    launch_event : EventFile, 
    finish_circuit_event : EventFile, 
    vqpu_id : int = 1,
    walltime : float = 86400, 
    arguments : str = "", 
    date : datetime.datetime = datetime.datetime.now() 
    ) -> None:
    '''
    @brief vqpu workflow that should be invoked with the appropriate task runner
    '''
    launch_event.clean()
    delete_vqpu_remote_yaml(vqpu_id)
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
      retries = 3, 
      retry_delay_seconds = 10, 
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
    ) -> None:
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
      flow_run_name = "circuits_flow_for_vqpu_{vqpu_id}_on-{date:%Y-%m-%d:%H:%M:%S}",
      description = "Running circutis on the vQPU with the appropriate task runner for launching circuits", 
      retries = 3, 
      retry_delay_seconds = 20, 
      log_prints = True,
      )
async def circuits_workflow(
    vqpu_event : EventFile, 
    circuit_event: EventFile,
    circuits : List[Callable],
    vqpu_id : int = 1,  
    arguments : str = "", 
    delay_before_start : float = 60.0, 
    date : datetime.datetime = datetime.datetime.now() 
    ) -> None:
    '''
    @brief vqpu workflow that should be invoked with the appropriate task runner. Mean to launch the vqpu
    '''
    if (delay_before_start < 0): delay_before_start = 0
    circuit_event.clean()
    logger = get_run_logger()
    logger.info(f'Delay of {delay_before_start} seconds before starting ... ')
    await asyncio.sleep(delay_before_start)
    logger.info(f'Waiting for vqpu-{vqpu_id} to start ... ')
    await vqpu_event.wait()
    artifact = await Artifact.get(key=f'remote{vqpu_id}')
    remote = dict(artifact)['data'].split('\n')[1]
    logger.info(f'vqpu-{vqpu_id} running, submitting circuits ...')
    for c in circuits:
        logger.info(f'Running {c.__name__}')
        future = await run_circuit.submit(
            circuitfunc = c,
            arguments = arguments,
            vqpu_id = vqpu_id, 
            remote = remote, 
            )
        results = await future.result()
        print(f'{c.__name__} return {results}')
    logger.info('Finished all running all circuits')
    circuit_event.set()
    return results

@task 
def circuit_event_clean(
    events : Dict[str,EventFile],
    vqpu_ids : List[int]
) -> None:
    for vqpu_id in vqpu_ids:
        key = f'vqpu_{vqpu_id}_circuits_finished'
        events[key].clean()

@task
async def startup_wait(delay : float) -> None:
    logger = get_run_logger()
    logger.info(f'Delay of {delay} seconds before starting ... ')
    await asyncio.sleep(delay)


@flow(name = "Circuits with multi-vQPU, GPU flow", 
      flow_run_name = "circuits_flow_for_vqpu_{vqpu_ids}_on-{date:%Y-%m-%d:%H:%M:%S}",
      description = "Running circutis on the vQPU with the appropriate task runner for launching circuits and also launches a gpu workflow, ", 
      retries = 3, 
      retry_delay_seconds = 20, 
      log_prints = True,
      )
async def circuits_with_nqvpuqs_workflow(
    task_runners : Dict[str, Any],
    events : Dict[str, EventFile], 
    circuits : Dict[str, List[Callable]],
    vqpu_ids : List[int],  
    arguments : str, 
    delay_before_start : float = 60.0, 
    date : datetime.datetime = datetime.datetime.now() 
    ) -> None:
    '''
    @brief a multi vqpu workflow launching circuits on multiple vqpus 
    and then possibly launching gpu and cpu tasks afterwards 
    '''
    if (delay_before_start < 0): delay_before_start = 0
    # circuit_event_clean.submit(events, vqpu_ids).result()
    # future = await startup_wait.submit(delay_before_start)
    # await future.result()
    for vqpu_id in vqpu_ids:
        key = f'vqpu_{vqpu_id}_circuits_finished'
        events[key].clean()
  
    logger = get_run_logger()
    logger.info(f'Delay of {delay_before_start} seconds before starting ... ')
    await asyncio.sleep(delay_before_start)

    logger = get_run_logger()
    logger.info(f'Waiting for vqpu-{vqpu_ids} to start ... ')

    # need to convert to a task group but for now 
    # just have for loop
    for vqpu_id in vqpu_ids:
        key = f'vqpu_{vqpu_id}'
        await events[key+'_launch'].wait()
        artifact = await Artifact.get(key=f'remote{vqpu_id}')
        remote = dict(artifact)['data'].split('\n')[1]
        logger.info(f'vqpu-{vqpu_id} running, submitting circuits ...')
        for c in circuits[key]:
            logger.info(f'Running {c.__name__}')
            future = await run_circuit.submit(
                circuitfunc = c,
                arguments = arguments,
                vqpu_id = vqpu_id, 
                remote = remote, 
                )
            results = await future.result()
            print(f'{c.__name__} return {results}')
            # and for now randomly launch a gpu and cpu flow based on 
            # some random number 
            if (np.random.uniform() > 0.5):
                await gpu_workflow.with_options(
                    task_runner = task_runners['gpu']
                )(arguments = arguments)
            for i in range(4):
                if (np.random.uniform() > 0.2):
                    await cpu_workflow.with_options(
                        task_runner = task_runners['cpu']
                    )(arguments = arguments)

        logger.info('Finished all running all circuits')
        events[key+'_circuits_finished'].set()
    return results

@task(retries = 10, 
      retry_delay_seconds = 2,
      timeout_seconds=3600,
      result_serializer="compressed/json"
      )
async def run_cpu(arguments: str) -> None:
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
async def run_gpu(arguments: str) -> None:
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
      flow_run_name = "cpu_flow_on-{date:%Y-%m-%d:%H:%M:%S}",
      description = "Running cpu flows", 
      retries = 3, 
      retry_delay_seconds = 10, 
      log_prints=True, 
      )
async def cpu_workflow(
    arguments : str = "",
    date : datetime.datetime = datetime.datetime.now() 
    ) -> None:
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
      flow_run_name = "gpu_flow_on-{date:%Y-%m-%d:%H:%M:%S}",
      description = "Running gpu flows", 
      retries = 3, 
      retry_delay_seconds = 10, 
      log_prints=True, 
      )
async def gpu_workflow(
    arguments : str = "",
    date : datetime.datetime = datetime.datetime.now() 
    ) -> None:
    '''
    @brief gpu workflow that should be invoked with the appropriate task runner
    '''
    logger = get_run_logger()
    logger.info("Launching GPU flow")
    # submit the task and wait for results 
    future = await run_gpu.submit(arguments)
    await future.result()
    logger.info('Finished GPU flow')
    
