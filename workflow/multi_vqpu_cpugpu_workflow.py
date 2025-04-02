"""
@brief This example shows how to structure a multi-vqpu workflow. 

This workflow spins up two or more vqpus and then has a workflow that runs cpu/gpu flows that also then spawn circuit flows. 

"""

import sys, os
sys.path.append(os.path.dirname(os.path.abspath(__file__))+'/vqpucommon/')
from time import sleep
import datetime
from typing import List, Set, Callable, Tuple, Dict
from options import vQPUWorkflow
from vqpuworkflow import HybridQuantumWorkflowBase, launch_vqpu_workflow, circuits_with_nqvpuqs_workflow, circuits_vqpu_workflow, cpu_workflow, gpu_workflow, postprocessing_histo_plot, run_cpu, run_circuits_once_vqpu_ready
from utils import EventFile, save_artifact
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
    myqpuworkflow : HybridQuantumWorkflowBase, 
    vqpu_ids_subset : List[int], 
    circuits : Dict[str, List[Callable | Tuple[Callable, Callable]]],
    circuitargs : str,  
    cpuexecs : List[str],
    cpuargs : List[str],
    gpuexecs : List[str], 
    gpuargs : List[str],
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
    futures = []
    for exec, args in zip(cpuexecs,cpuargs):
        futures.append(await run_cpu.submit(exec = exec, arguments = args))
    for f in futures:
        await f.result()
    tasks = {
        'gpu': list(), 
        'cpu': list(), 
        'qpu': dict(),
        }
    for vqpu_id in vqpu_ids_subset:
        tasks['qpu'][vqpu_id] = list()

    circflow = circuits_vqpu_workflow.with_options(
                    task_runner = myqpuworkflow.taskrunners['circuit'],
                )
    async with asyncio.TaskGroup() as tg:
        for i in range(max_num_gpu_launches):
            if (np.random.uniform() > 0.5):
                tasks['gpu'].append(tg.create_task(gpu_workflow.with_options(task_runner = myqpuworkflow.taskrunners['gpu'])(myqpuworkflow=myqpuworkflow, execs = gpuexecs, arguments = gpuargs)))
                if (np.random.uniform() > 0.75):
                    tasks['cpu'].append(tg.create_task(cpu_workflow.with_options(
                        task_runner = myqpuworkflow.taskrunners['cpu']
                    )(myqpuworkflow, execs = cpuexecs, arguments = cpuexecs)))
        # either spin up real vqpu
        for vqpu_id in vqpu_ids_subset:
            if (np.random.uniform() > 0.5):
                tasks['qpu'][vqpu_id] = tg.create_task(circflow(
                    myqpuworkflow = myqpuworkflow,
                    vqpu_id = vqpu_id,
                    circuits = circuits, 
                    arguments = circuitargs, 
                    circuits_complete = True, 
                    )
                )
            else:
                    myqpuworkflow.events[f'vqpu_{vqpu_id}_circuits_finished'].set() 

    logger.info("Finished CPU with QPU flow")

@flow(name = "Multi-vQPU Test", 
    flow_run_name = "Mulit-vQPU_test_{vqpu_ids}_on-{date:%Y-%m-%d:%H:%M:%S}",
    description = "Running a multi-(v)QPU+CPU+GPU hybrid workflow", 
    retries = 3, retry_delay_seconds = 10, 
    log_prints=True, 
    )
async def workflow(
    myqpuworkflow : HybridQuantumWorkflowBase, 
    circuitargs : str,  
    cpuexecs : List[str],
    cpuargs : List[str],
    gpuexecs : List[str], 
    gpuargs : List[str],
    vqpu_walltimes : List[float], 
    date : datetime.datetime = datetime.datetime.now() 
    ):
    """
    @brief overall workflow for hydrid multi-(v)QPU+CPU+GPU
    """
    MAXWALLTIME : float = 86400.0
    if len(vqpu_walltimes) < len(myqpuworkflow.vqpu_ids):
        num_to_add = len(myqpuworkflow.vqpu_ids)-len(vqpu_walltimes)
        for i in range(num_to_add):
            vqpu_walltimes.append(MAXWALLTIME)

    logger = get_run_logger()
    logger.info("Running hybrid multi-(v)QPU workflow")

    vqpuflows = dict()
    circuits = dict() 
    circuitflows = dict()
    for vqpu_id in self.vqpu_ids:
        if vqpu_id > 1 and vqpu_id < 3:
            circuits[f'vqpu_{vqpu_id}'] = [noisy_circuit, noisy_circuit, noisy_circuit]
        elif vqpu_id > 3:
            circuits[f'vqpu_{vqpu_id}'] = [(noisy_circuit, postprocessing_histo_plot)]
        else:
            circuits[f'vqpu_{vqpu_id}'] = [(noisy_circuit, postprocessing_histo_plot)]


        # lets define the flows with the appropriate task runners 
        # this would be for the real vqpu 
        vqpuflows[f'vqpu_{vqpu_id}'] = launch_vqpu_workflow.with_options(
            task_runner = myqpuworkflow.taskrunners['vqpu'],
            )
    circuitflows = circuits_with_nqvpuqs_workflow.with_options(
        task_runner = myqpuworkflow.taskrunners['circuit'],
    )
    othercircuitflows = cpu_with_random_qpu_workflow.with_options(
        task_runner = myqpuworkflow.task_runners['cpu'],
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
                    myqpuworkflow = myqpuworkflow, 
                    walltime = vqpu_walltime,  
                    vqpu_id = vqpu_id, 
                    ))

        # silly change so that any vqpu_ids past 3 are 
        # not provided to circuitflows but rather 
        # set aside to the cpu flow that can spawn vqpus
        tg.create_task(circuitflows(
            myqpuworkflow = myqpuworkflow,
            circuits = circuits,
            vqpu_ids = circ_vqpu_ids, 
            arguments = circuitargs, 
        ))
        tg.create_task(othercircuitflows(
            myqpuworkflow = myqpuworkflow,
            circuits = circuits,
            vqpu_ids_subset = other_vqpu_ids, 
            circuitargs = circuitargs,  
            cpuexecs = cpuexecs,
            cpuargs = cpuargs,
            gpuexecs = gpuexecs, 
            gpuargs = gpuargs,
        ))

    for k in myqpuworkflow.events.keys():
        myqpuworkflow.events[k].clean()

    logger.info("Finished hybrid multi-(v)QPU workflow")


def wrapper_to_async_flow(
        circuitargs : str = '',  
        cpuexecs : List[str] = [
            '/software/projects/pawsey0001/pelahi/profile_util/examples/openmp/bin/openmpvec_cpp', 
            '/software/projects/pawsey0001/pelahi/profile_util/examples/openmp/bin/openmpvec_cpp'
            ],
        cpuargs : List[str] = [
            '134217728',
            '1073741824', 
        ],
        gpuexecs : List[str] = [
            '/software/projects/pawsey0001/pelahi/profile_util/examples/gpu-openmp/bin/gpu-openmp',
            '/software/projects/pawsey0001/pelahi/profile_util/examples/gpu-openmp/bin/gpu-openmp',
            '/software/projects/pawsey0001/pelahi/profile_util/examples/gpu-openmp/bin/gpu-openmp',
        ], 
        gpuargs : List[str] = [
            '134217728,10',
            '134217728,1',
            '1073741824,10'
        ],
):
    """
    @brief run the workflow with the appropriate task runner
    """
    myflow = HybridQuantumWorkflowBase(
        cluster = 'ella-qb', 
        vqpu_ids = [1, 2, 3, 16], 
    )
    myflow_message = print(myflow)

    # Serialize the object to a JSON string
    import json
    json_string = json.dumps(myflow.to_dict())
    print("Serialized JSON:", json_string)

    # Deserialize the JSON string back into an object
    loaded_object = HybridQuantumWorkflowBase.from_dict(json.loads(json_string))
    loaded_object_message = print(loaded_object)
    print(myflow_message == loaded_object_message)

    # asyncio.run(launch_vqpu_workflow.with_options(task_runner = myflow.taskrunners['vqpu'])(myqpuworkflow=myflow, vqpu_id = 1, walltime = 400))

    # asyncio.run(myflow.workflow(
    #     myqpuworkflow=myflow,
    #     circuitargs=circuitargs,
    #     cpuexecs=cpuexecs,
    #     cpuargs= cpuargs,
    #     gpuexecs=gpuexecs,
    #     gpuargs=gpuargs,
    #     vqpu_walltimes=[86400, 500, 1000, 1000])
    # )

def cli(
        local_run : bool = False, 
    ) -> None:
    import logging

    logger = logging.getLogger('vQPU')
    logger.setLevel(logging.INFO)
    wrapper_to_async_flow()

if __name__ == '__main__':
    cli(local_run = True)
