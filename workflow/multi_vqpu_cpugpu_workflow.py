'''
@brief This example shows how to structure a multi-vqpu workflow. 

This workflow spins up two or more vqpus and then has a workflow that runs cpu/gpu flows that also then spawn circuit flows. 

'''


from time import sleep
import datetime
from typing import List, Set, Callable, Tuple, Dict
from vqpucommon.clusters import get_dask_runners
from vqpucommon.options import vQPUWorkflow
from vqpucommon.vqpuworkflow import launch_vqpu_workflow, circuits_with_nqvpuqs_workflow, circuits_workflow, cpu_workflow, gpu_workflow, postprocessing_histo_plot, run_cpu, run_circuits_once_vqpu_ready
from vqpucommon.utils import EventFile, save_artifact
from circuits.qristal_circuits import simulator_setup, noisy_circuit
import asyncio
from prefect import flow
from prefect_dask import DaskTaskRunner
from prefect.logging import get_run_logger
import numpy as np


@flow(name = "Example CPU flow with QPU-circuit sumbission", 
      flow_run_name = "cpu_qpu_flow_on_vqpu_{vqpu_ids}-{date:%Y-%m-%d:%H:%M:%S}",
      description = "Running CPU flows than can also create QPU-circuits for submission", 
      retries = 3, 
      retry_delay_seconds = 10, 
      log_prints=True, 
      )
async def cpu_with_random_qpu_workflow(
    task_runners : Dict[str, DaskTaskRunner | Dict[str,str]],
    events : Dict[str, EventFile],
    circuits : Dict[str, List[Callable | Tuple[Callable, Callable]]],
    vqpu_ids : List[int],
    arguments : str, 
    max_num_gpu_launches : int = 4,
    date : datetime.datetime = datetime.datetime.now() 
    ) -> None:
    """Flow for running cpu based programs.

        arguments (str): string of arguments to pass extra options to run cpu 
    """
    logger = get_run_logger()
    logger.info("Launching CPU Master with QPU flow")
    sleepval = 5.0+np.random.uniform()*10
    logger.info(f"Sleeping for {sleepval}")
    await asyncio.sleep(sleepval)
    # submit the task and wait for results 
    future = await run_cpu.submit(arguments)
    await future.result()
    tasks = {
        'gpu': list(), 
        'cpu': list(), 
        'qpu': dict(),
        }
    for vqpu_id in vqpu_ids:
        tasks['qpu'][vqpu_id] = list()

    circflow = circuits_workflow.with_options(
                    task_runner = task_runners['circuit'],
                )
    async with asyncio.TaskGroup() as tg:
        for i in range(max_num_gpu_launches):
            if (np.random.uniform() > 0.5):
                tasks['gpu'].append(tg.create_task(gpu_workflow.with_options(
                    task_runner = task_runners['gpu']
                )(arguments = arguments)))
                if (np.random.uniform() > 0.75):
                    tasks['cpu'].append(tg.create_task(cpu_workflow.with_options(
                        task_runner = task_runners['cpu']
                    )(arguments = arguments)))
        # either spin up real vqpu
        for vqpu_id in vqpu_ids:
            if (np.random.uniform() > 0.5):
                tasks['qpu'][vqpu_id] = tg.create_task(circflow(
                    vqpu_event = events[f'vqpu_{vqpu_id}_launch'], 
                    circuit_event = events[f'vqpu_{vqpu_id}_circuits_finished'], 
                    circuits = circuits[f'vqpu_{vqpu_id}'],
                    vqpu_id = vqpu_id,
                    arguments = arguments, 
                    circuits_complete = True, 
                    )
                )
            else :
                    events[f'vqpu_{vqpu_id}_circuits_finished'].set() 

    logger.info("Finished CPU with QPU flow")


@flow(name = "Multi-vQPU Test", 
      flow_run_name = "Mulit-vQPU_test_{vqpu_ids}_on-{date:%Y-%m-%d:%H:%M:%S}",
      description = "Running a multi-(v)QPU+CPU+GPU hybrid workflow", 
      retries = 3, retry_delay_seconds = 10, 
      log_prints=True, 
      )
async def workflow(
    task_runners : dict, 
    arguments: str , 
    vqpu_ids : Set[int] = [1,2], 
    vqpu_walltimes : List[float] = [86400.0, 500.0], 
    add_other_tasks : bool = True, 
    date : datetime.datetime = datetime.datetime.now() 
    ):
    '''
    @brief overall workflow for hydrid multi-(v)QPU+CPU+GPU
    '''
    MAXWALLTIME : float = 86400.0
    if len(vqpu_walltimes) < len(vqpu_ids):
        num_to_add = len(vqpu_ids)-len(vqpu_walltimes)
        for i in range(num_to_add):
            vqpu_walltimes.append(MAXWALLTIME)

    logger = get_run_logger()
    logger.info("Running hybrid multi-(v)QPU workflow")

    vqpuflows = dict()
    events = dict()
    circuits = dict() 
    circuitflows = dict()
    for vqpu_id in vqpu_ids:
        # create events: one for the vqpu is running, the other for all circuits finished
        events[f'vqpu_{vqpu_id}_launch'] = EventFile(name = f'vqpu_{vqpu_id}_launch', loc = './events/') 
        events[f'vqpu_{vqpu_id}_circuits_finished'] = EventFile(name = f'vqpu_{vqpu_id}_circuits_finished', loc = './events/') 
        if vqpu_id > 1 and vqpu_id < 3:
            circuits[f'vqpu_{vqpu_id}'] = [noisy_circuit, noisy_circuit, noisy_circuit]
        elif vqpu_id > 3:
            circuits[f'vqpu_{vqpu_id}'] = [(noisy_circuit, postprocessing_histo_plot)]
        else:
            circuits[f'vqpu_{vqpu_id}'] = [(noisy_circuit, postprocessing_histo_plot)]


        # lets define the flows with the appropriate task runners 
        # this would be for the real vqpu 
        vqpuflows[f'vqpu_{vqpu_id}'] = launch_vqpu_workflow.with_options(
            task_runner = task_runners['vqpu'],
            )
    circuitflows = circuits_with_nqvpuqs_workflow.with_options(
        task_runner = task_runners['circuit'],
    )
    othercircuitflows = cpu_with_random_qpu_workflow.with_options(
        task_runner = task_runners['cpu'],
    )

    # since the set is just used to make sure ids are unique, 
    # lets convert it back to a list for easy use
    vqpu_ids = list(vqpu_ids)

    # silly change so that any vqpu_ids past 3 are 
    # not provided to circuitflows but rather 
    # set aside to the cpu flow that can spawn vqpus
    circ_vqpu_ids = list()
    other_vqpu_ids = list()
    for vqpu_id in vqpu_ids:
        if vqpu_id >= 3:
            other_vqpu_ids.append(vqpu_id)
        else:
            circ_vqpu_ids.append(vqpu_id)
            
    async with asyncio.TaskGroup() as tg:
        # either spin up real vqpu
        for i in range(len(vqpu_ids)):
            vqpu_id = vqpu_ids[i]
            vqpu_walltime = vqpu_walltimes[i]
            tg.create_task(
                vqpuflows[f'vqpu_{vqpu_id}'](
                    launch_event = events[f'vqpu_{vqpu_id}_launch'],
                    finish_circuit_event = events[f'vqpu_{vqpu_id}_circuits_finished'],
                    walltime = vqpu_walltime,  
                    vqpu_id = vqpu_id, 
                    arguments = arguments, 
                    ))

        # silly change so that any vqpu_ids past 3 are 
        # not provided to circuitflows but rather 
        # set aside to the cpu flow that can spawn vqpus
        tg.create_task(circuitflows(
            task_runners = task_runners, 
            events = events, 
            circuits = circuits,
            vqpu_ids = circ_vqpu_ids, 
            arguments = arguments, 
        ))
        tg.create_task(othercircuitflows(
            task_runners = task_runners, 
            events = events, 
            circuits = circuits,
            vqpu_ids = other_vqpu_ids, 
            arguments = arguments, 
        ))

    for k in events.keys():
        events[k].clean()

    logger.info("Finished hybrid multi-(v)QPU workflow")

def wrapper_to_async_flow(arguments: str):
    '''
    @brief run the workflow with the appropriate task runner
    '''
    task_runners = get_dask_runners(cluster='ella-qb')
    asyncio.run(workflow(task_runners, arguments, vqpu_ids = set([1,2,3,16]), vqpu_walltimes=[86400, 500, 1000, 1000]))

def cli(
        local_run : bool = False, 
    ) -> None:
    import logging

    logger = logging.getLogger('vQPU')
    logger.setLevel(logging.INFO)

    # parser = get_parser()
    # args = parser.parse_args()
    arguments : str = ''
    arguments += ' --vqpu-exec=qcstack '
    arguments += ' --gpu-mpi-exec=/software/projects/pawsey0001/pelahi/profile_util/examples/gpu-mpi/bin/gpu-mpi-comm '
    arguments += ' --gpu-mpi-args=24.0,2 '
    arguments += ' --gpu-exec=/software/projects/pawsey0001/pelahi/profile_util/examples/gpu-openmp/bin/gpu-openmp '
    arguments += ' --gpu-args=134217728,10 '
    arguments += ' --cpu-exec=/software/projects/pawsey0001/pelahi/profile_util/examples/openmp/bin/openmpvec_cpp '
    arguments += ' --cpu-args=134217728 '

    wrapper_to_async_flow(arguments)
    #flow_wrapper_to_async_flow(arguments).visualize()

if __name__ == '__main__':
    cli(local_run = True)
