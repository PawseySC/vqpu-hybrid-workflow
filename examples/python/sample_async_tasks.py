'''
@file sample_async_flows.py
@brief example of how to run asynchronous flows, tasks and nested flows with different runners

For this work we will be using Dask backed workers to perform the compute
operations.
'''

import sys, os
# update the path to include desired python modules like clusters utils 
scriptpath = os.path.dirname(os.path.abspath(__file__))
sys.path.append(scriptpath+'/../../workflow/')
from mycommon.clusters import get_dask_runners
# note that if for task.submit to work, it must be serializable and so any functions called in the task 
# must be accessible based on the PYTHONPATH, thus make sure utility functions are located as a directory 
# from where the script is called.
from mycommon.utils import save_artifact, run_a_srun_process, run_a_process, SlurmInfo, get_job_info, EventFile
# now grab useful libraries
from time import sleep, process_time
import numpy as np
from typing import Any, Dict, List, Optional, Union
import subprocess
# and then prefect objects
from prefect import flow, task
from prefect.context import FlowRunContext
from prefect_dask.task_runners import DaskTaskRunner
from prefect.task_runners import ConcurrentTaskRunner
#from prefect.task_runners import SequentialTaskRunner
from prefect.logging import get_run_logger
import asyncio
import inspect 

# now define some tasks 
@task 
async def subprocess_async_srun() -> None:
    '''
    It looks like there are issues using `srun` when passing to the slurmcluster dasktaskrunner 
    Though it might provide better cpu affinity, it does cause issues and errors when running
    '''
    logger = get_run_logger()
    logger.info('Running subprocesses with srun')
    cmds = ['/software/projects/pawsey0001/pelahi/profile_util/examples/openmp/bin/openmpvec_cpp', '134217728']
    process = run_a_srun_process(cmds, ['--ntasks=1 --cpus-per-task=2'], logger)

@task
def subprocess_srun() -> None:
    logger = get_run_logger()
    logger.info('Running subprocesses with srun')
    cmds = ['/software/projects/pawsey0001/pelahi/profile_util/examples/openmp/bin/openmpvec_cpp', '134217728']
    process = run_a_srun_process(cmds, ['--ntasks=1 --cpus-per-task=2'], logger)

@task 
async def subproc_async() -> None:
    logger = get_run_logger()
    logger.info('Running subprocesses with async')
    cmds = ['/software/projects/pawsey0001/pelahi/profile_util/examples/openmp/bin/openmpvec_cpp', '134217728']
    process = subprocess.run(cmds, capture_output=False, text=True)

@task 
def subproc() -> None:
    logger = get_run_logger()
    logger.info('Running subprocesses without async')
    cmds = ['/software/projects/pawsey0001/pelahi/profile_util/examples/openmp/bin/openmpvec_cpp', '134217728']
    process = subprocess.run(cmds, capture_output=False, text=True)

@task 
async def subproc_async_event_1(emitter: asyncio.Event,
                                irunproc : bool = False) -> None:
    logger = get_run_logger()
    logger.info('Running subprocesses with async')
    cmds = ['/software/projects/pawsey0001/pelahi/profile_util/examples/openmp/bin/openmpvec_cpp', '134217728']
    if irunproc:
        process = subprocess.run(cmds, capture_output=False, text=True)
    logger.info('Now going to sleep before emitting')
    await asyncio.sleep(20)
    logger.info('Emitting')
    emitter.set()
    logger.info('Emitted')
    logger.info('Done')

@task 
async def subproc_async_event_2(emitter: asyncio.Event, 
                                receive: asyncio.Event,
                                irunproc : bool = False) -> None:
    logger = get_run_logger()
    logger.info('Running subprocesses with async')
    logger.info('Waiting for an event ')
    await receive.wait()
    logger.info('Now doing the second subprocess')
    cmds = ['/software/projects/pawsey0001/pelahi/profile_util/examples/openmp/bin/openmpvec_cpp', '134217728']
    if irunproc:
        process = subprocess.run(cmds, capture_output=False, text=True)
    logger.info('Emitting')
    emitter.set()
    logger.info('Emitted')
    cmds = ['/software/projects/pawsey0001/pelahi/profile_util/examples/openmp/bin/openmpvec_cpp', '100']
    if irunproc:
        process = subprocess.run(cmds, capture_output=False, text=True)
    logger.info('Done')

@task 
async def subproc_async_event_3(receive: asyncio.Event,
                                irunproc : bool = False) -> None:
    logger = get_run_logger()
    logger.info('Running subprocesses with async')
    cmds = ['/software/projects/pawsey0001/pelahi/profile_util/examples/openmp/bin/openmpvec_cpp', '1342177280']
    logger.info('Waiting for an event ')
    await receive.wait()
    logger.info('Now proceeding')
    if irunproc: 
        process = subprocess.run(cmds, capture_output=False, text=True)
    logger.info('Done')


@task 
async def subproc_async_EventFile_1(emitter: EventFile,
                                irunproc : bool = False) -> None:
    logger = get_run_logger()
    logger.info('Running subprocesses with async')
    cmds = ['/software/projects/pawsey0001/pelahi/profile_util/examples/openmp/bin/openmpvec_cpp', '134217728']
    if irunproc:
        process = subprocess.run(cmds, capture_output=False, text=True)
    logger.info('Now going to sleep before emitting')
    await asyncio.sleep(20)
    logger.info('Emitting')
    emitter.set()
    logger.info('Emitted')
    logger.info('Done')

@task 
async def subproc_async_EventFile_2(emitter: EventFile, 
                                receive: EventFile,
                                irunproc : bool = False) -> None:
    logger = get_run_logger()
    logger.info('Running subprocesses with async')
    logger.info('Waiting for an event ')
    await receive.wait()
    logger.info('Now doing the second subprocess')
    cmds = ['/software/projects/pawsey0001/pelahi/profile_util/examples/openmp/bin/openmpvec_cpp', '134217728']
    if irunproc:
        process = subprocess.run(cmds, capture_output=False, text=True)
    logger.info('Emitting')
    emitter.set()
    logger.info('Emitted')
    cmds = ['/software/projects/pawsey0001/pelahi/profile_util/examples/openmp/bin/openmpvec_cpp', '100']
    if irunproc:
        process = subprocess.run(cmds, capture_output=False, text=True)
    logger.info('Done')

@task 
async def subproc_async_EventFile_3(receive: EventFile,
                                irunproc : bool = False) -> None:
    logger = get_run_logger()
    logger.info('Running subprocesses with async')
    cmds = ['/software/projects/pawsey0001/pelahi/profile_util/examples/openmp/bin/openmpvec_cpp', '1342177280']
    logger.info('Waiting for an event ')
    await receive.wait()
    logger.info('Now proceeding')
    if irunproc: 
        process = subprocess.run(cmds, capture_output=False, text=True)
    logger.info('Done')

@task
def sillyfunc(val, n:int = 100, Niter:int = 1000000):
    logger = get_run_logger()
    logger.info(f'sillyfunc task {val}')
    start = process_time()
    x, y = np.ones([n,n]), np.zeros([n,n])+0.5
    for i in range(Niter):
        z=x*y
    result = process_time() - start
    logger.info(f'Silly func took {result}')
    return result 

@task
async def task_1():
    print("Running Task 1")
    future = sillyfunc.submit('1')
    val = future.result()
    return f"Task 1 Complete {val}"

@task
async def task_2():
    print("Running Task 2")
    future = sillyfunc.submit('2')
    val = future.result()
    return f"Task 2 Complete {val}"

@flow
async def main_flow_concurrent_tasks():
    # Run subflows concurrently
    result_1, result_2 = await asyncio.gather(task_1(), task_2())
    print(result_1, result_2)
   
@task
async def sillyfunc_async(val, n:int = 100, Niter:int = 1000000) -> float:
    logger = get_run_logger()
    logger.info(f'sillyfunc task {val}')
    start = process_time()
    x, y = np.ones([n,n]), np.zeros([n,n])+0.5
    for i in range(Niter):
        z=x*y
    result = process_time() - start
    logger.info(f'Silly func took {result}')
    return result 


@flow
async def subflow_1(iasynctask : bool = True) -> None:
    print("Running Subflow 1")
    val = 0
    if iasynctask:
        future = await sillyfunc_async.submit('1')
        val = await future.result()
        future = await subproc_async.submit()
        await future.result()
    else: 
        val = sillyfunc.submit('1').result()
        subproc.submit().result()
    print(f"Subflow 1 Complete {val}")


@flow
async def subflow_2(iasynctask : bool = True) -> None:
    print("Running Subflow 2")
    val = 0
    if iasynctask:
        future = await sillyfunc_async.submit('1')
        val = await future.result()
        future = await subproc_async.submit()
        await future.result()
    else: 
        val = sillyfunc.submit('2').result()
        subproc.submit().result()
    print(f"Subflow 2 Complete {val}")

@flow
async def main_flow_concurrent_flows(task_runners: dict) -> None:
    # Run subflows concurrently
    flow1 = subflow_1.with_options(task_runner = task_runners['gpu'])
    flow2 = subflow_2.with_options(task_runner = task_runners['cpu'])
    async with asyncio.TaskGroup() as tg:
        tg.create_task(flow1()) 
        tg.create_task(flow2())
    
@flow
async def subflow_1_event(events : Dict[str,asyncio.Event]) -> None:
    print("Running Subflow 1")
    future3 = await subproc_async_event_3.submit(events['subproc2_start'])
    future1 = await subproc_async_event_1.submit(events['subproc2_start'])
    await future1.result()
    await future3.result()
    print(f"Subflow 1 Complete")


@flow
async def subflow_2_event(events : Dict[str,asyncio.Event]) -> None:
    print("Running Subflow 2")
    future = await subproc_async_event_2.submit(events['subproc3_start'], events['subproc2_start'])
    await future.result()
    print(f"Subflow 2 Complete")

@flow
async def main_flow_concurrent_flows_events(task_runners: dict) -> None:
    # Run subflows concurrently
    # flow1 = subflow_1_event.with_options(task_runner = task_runners['gpu'])
    # flow2 = subflow_2_event.with_options(task_runner = task_runners['cpu'])
    events = {'subproc2_start':asyncio.Event(), 'subproc3_start':asyncio.Event()}
    async with asyncio.TaskGroup() as tg:
        # tg.create_task(flow1(events)) 
        # tg.create_task(flow2(events))
        tg.create_task(subflow_1_event.with_options(task_runner = ConcurrentTaskRunner)(events)) 
        tg.create_task(subflow_2_event.with_options(task_runner = ConcurrentTaskRunner)(events))

@flow
async def subflow_1_EventFile(events : Dict[str,EventFile]) -> None:
    print("Running Subflow 1")
    future3 = await subproc_async_event_3.submit(events['subproc2_start'])
    future1 = await subproc_async_event_1.submit(events['subproc2_start'])
    await future1.result()
    await future3.result()
    print(f"Subflow 1 Complete")


@flow
async def subflow_2_EventFile(events : Dict[str,EventFile]) -> None:
    print("Running Subflow 2")
    future = await subproc_async_event_2.submit(events['subproc3_start'], events['subproc2_start'])
    await future.result()
    print(f"Subflow 2 Complete")

@flow
async def main_flow_concurrent_flows_EventFiles(task_runners: dict) -> None:
    # Run subflows concurrently
    flow_run_context = FlowRunContext.get()
    flow_id = flow_run_context.flow_run.id
    logger = get_run_logger()
    logger.info(f'Flow {flow_id}')

    flow1 = subflow_1_EventFile.with_options(task_runner = task_runners['gpu'])
    flow2 = subflow_2_EventFile.with_options(task_runner = task_runners['cpu'])
    events = {
        'subproc2_start':EventFile(name = 'subproc2_start', loc ='/software/projects/pawsey0001/pelahi/vqpu-tests/examples/python/'), 
        'subproc3_start':EventFile(name = 'subproc3_start', loc ='/software/projects/pawsey0001/pelahi/vqpu-tests/examples/python/'), 
    }
    async with asyncio.TaskGroup() as tg:
        tg.create_task(flow1(events)) 
        tg.create_task(flow2(events))

    for k in events.keys():
        events[k].clean()

@flow()
def nested_subflow_1():
    print("Running Nested Subflow 1")
    future = sillyfunc.submit('1')
    val = future.result()
    subproc.submit().result()
    print(f"Nested Subflow 1 Complete {val}")


@flow
def nested_subflow_2():
    print("Running Nested Subflow 2")
    future = sillyfunc.submit('2')
    val = future.result()
    subproc.submit().result()
    print(f"Nested Subflow 2 Complete {val}")

@flow
def main_flow_nested_flows(task_runners : dict):
    # Run subflows concurrently
    # note that here you should pass the task runners you want the subflows to be invoked with 
    # otherwise they will default to the local cluster serial task runner
    nested_subflow_1.with_options(task_runner = task_runners['cpu'])()
    nested_subflow_2.with_options(task_runner = task_runners['gpu'])()

if __name__ == "__main__":
    # get some dask task runners for a cluster (here ella)
    task_runners = get_dask_runners('ella')
    # # try async with events 
    # asyncio.run(main_flow_concurrent_flows_events(task_runners))

    # # try async with events using EventFile
    asyncio.run(main_flow_concurrent_flows_EventFiles(task_runners))

    # # then try running concurrent flows with different runners 
    # asyncio.run(main_flow_concurrent_flows(task_runners))

    # # then try concurrent tasks 
    # asyncio.run(main_flow_concurrent_tasks.with_options(task_runner = task_runners['cpu'])())

    # # finally call nested flows, fist constructing a new flow from the with options and then call it
    # newflow = main_flow_nested_flows.with_options(task_runner = task_runners['gpu'])
    # newflow(task_runners)

