'''
@brief This example shows how to structure a multi-vqpu workflow. 

This workflow spins up two or more vqpus and then has a workflow that runs cpu/gpu flows that also then spawn circuit flows. 

'''


from time import sleep
from typing import List, NamedTuple, Optional, Tuple, Union, Generator
from vqpucommon.clusters import get_dask_runners
from vqpucommon.options import vQPUWorkflow
from vqpucommon.vqpuworkflow import launch_vqpu_workflow, circuits_with_nqvpuqs_workflow, circuits_workflow, cpu_workflow, gpu_workflow
from vqpucommon.utils import EventFile, save_artifact
from circuits.qristal_circuits import simulator_setup, noisy_circuit
import asyncio
from prefect import flow
from prefect.logging import get_run_logger
import numpy as np

@flow(name = "Multi-vQPU Test", 
      description = "Running a multi-(v)QPU+CPU+GPU hybrid workflow", 
      retries = 3, retry_delay_seconds = 10, 
      log_prints=True, 
      )
async def workflow(
    task_runners : dict, 
    arguments: str , 
    vqpu_ids : List[int] = [1,2], 
    vqpu_walltimes : List[float] = [86400.0, 500.0], 
    add_other_tasks : bool = True, 
    ):
    '''
    @brief overall workflow for hydrid multi-(v)QPU+CPU+GPU
    '''
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
        circuits[f'vqpu_{vqpu_id}'] = [noisy_circuit, noisy_circuit, noisy_circuit]

        # lets define the flows with the appropriate task runners 
        # this would be for the real vqpu 
        vqpuflows[f'vqpu_{vqpu_id}'] = launch_vqpu_workflow.with_options(
            task_runner = task_runners['vqpu'],
            )
    #     # lets define the flows with the appropriate task runners 
    #     circuitflows[f'vpuq_{vqpu_id}'] = circuits_workflow.with_options(
    #         task_runner = task_runners['circuit'],
    #         # want to set some options for the generic task runner here.
    #         )
    # gpuflow = gpu_workflow.with_options(
    #     task_runner = task_runners['gpu'],
    #     # want to set some options for the gpu task runner here.
    #     )
    # cpuflow = cpu_workflow.with_options(
    #     task_runner = task_runners['cpu'],
    #     # want to set some options for the cpu task runner here.
    #     )

    circuitflows = circuits_with_nqvpuqs_workflow.with_options(
            task_runner = task_runners['circuit'],
    ) 

    print(type(events))
    for k in events.keys():
        print(k, type(events[k]))
    print(type(circuits))

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
        #     tg.create_task(
        #         circuitflows[f'vqpu_{vqpu_id}'](
        #             vqpu_event = events[f'vqpu_{vqpu_id}_launch'], 
        #             circuit_event = events[f'circuits_finished_vqpu_{vqpu_id}'], 
        #             arguments = arguments, 
        #             vqpu_id = vqpu_id,
        #             circuits = circuits
        #         ))
        # if add_other_tasks:
        #     tg.create_task(cpuflow(arguments))
        #     tg.create_task(gpuflow(arguments))

        tg.create_task(circuitflows(
            task_runners = task_runners, 
            events = events, 
            circuits = circuits,
            vqpu_ids = vqpu_ids, 
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
    asyncio.run(workflow(task_runners, arguments))

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
