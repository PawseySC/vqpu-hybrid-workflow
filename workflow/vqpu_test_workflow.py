'''
@brief This example shows how to structure a vqpu workflow. 
In the future, will add circuits that turn on noise in a simulation, and how to modify the default noise model used. Currently circuits run by default do nothing but test orchestration. 

'''


from time import sleep
from typing import List, Dict, NamedTuple, Optional, Tuple, Union, Generator, Any
from vqpucommon.clusters import get_dask_runners
from vqpucommon.options import vQPUWorkflow
from vqpucommon.vqpuworkflow import launch_vqpu_workflow, launch_vqpu_test_workflow, circuits_workflow, circuits_with_processing_workflow, cpu_workflow, gpu_workflow, postprocessing_histo_plot, upload_image_as_artifact
from vqpucommon.utils import EventFile, save_artifact
from circuits.qristal_circuits import simulator_setup, noisy_circuit
import asyncio
from prefect import flow, task, get_client
from prefect.logging import get_run_logger
import numpy as np

# some silly circuits that don't connect to vqpu
def silly_test(remote : str, arguments : str):
    print('this is the silly test')
    sleep(10)
    return ['silly']

def foobar(remote : str, arguments : str):
    print('this is the foo bar')
    result = np.ones([100,100])*np.zeros([100,100])
    print(f'foobar {np.average(result)}')
    return np.average(result)

def sillypost(data : Dict[str, int], arguments : str):
    return data 

@flow(name = "Basic vQPU Test", 
      description = "Running a (v)QPU+CPU+GPU hybrid workflow", 
      retries = 3, retry_delay_seconds = 10, 
      log_prints=True, 
      )
async def workflow(
    task_runners : dict, 
    arguments: str = "", 
    vqpu_id : int = 1, 
    vqpu_walltime : float = 86400.0, 
    run_complex_circuits : bool = True,
    add_other_tasks : bool = True, 
    run_post : bool = True,
    ):
    '''
    @brief overall workflow for hydrid (v)QPU+CPU+GPU
    '''
    logger = get_run_logger()
    logger.info("Running hybrid (v)QPU workflow")
    
    subflows = []
    # create events: one for the vqpu is running, the other for all circuits finished
    events = {
        'vqpu_launch':EventFile(name = 'vqpu_launch', loc = './events/'), 
        'circuits_finished':EventFile(name = 'circuits_finished', loc = './events/'), 
        }

    # lets define the flows with the appropriate task runners 
    # this would be for the real vqpu 
    vqpuflow = launch_vqpu_workflow.with_options(
        task_runner = task_runners['vqpu'],
        )    
    vqputestflow = launch_vqpu_test_workflow.with_options(
        task_runner = task_runners['vqpu'],
        )    
    # lets define the flows with the appropriate task runners 
    if run_post:
        circuitflow = circuits_with_processing_workflow.with_options(
            task_runner = task_runners['circuit'],
            # want to set some options for the generic task runner here.
            )
    else:
        circuitflow = circuits_workflow.with_options(
            task_runner = task_runners['circuit'],
            # want to set some options for the generic task runner here.
            )
    gpuflow = gpu_workflow.with_options(
        task_runner = task_runners['gpu'],
        # want to set some options for the gpu task runner here.
        )
    cpuflow = cpu_workflow.with_options(
        task_runner = task_runners['cpu'],
        # want to set some options for the cpu task runner here.
        )

    if run_post:
        circuits = [(silly_test, sillypost), (foobar, sillypost)]
        if run_complex_circuits:
            circuits = [(noisy_circuit, postprocessing_histo_plot)]

    else:
        circuits = [silly_test, foobar]
        if run_complex_circuits:
            # need to add more complex circuits
            circuits = [noisy_circuit]

    async with asyncio.TaskGroup() as tg:
        # either spin up real vqpu
        tg.create_task(vqpuflow(
                    launch_event = events['vqpu_launch'],
                    finish_circuit_event = events['circuits_finished'],
                    arguments = arguments, 
                    walltime = vqpu_walltime,  
                    vqpu_id = vqpu_id))
        if run_post:
            tg.create_task(circuitflow(vqpu_event = events['vqpu_launch'], 
                                    circuit_event = events['circuits_finished'], 
                                    arguments = arguments, 
                                    vqpu_id = vqpu_id,
                                    circuitsandprocesing = circuits
                                    ))
        else:
            tg.create_task(circuitflow(vqpu_event = events['vqpu_launch'], 
                                    circuit_event = events['circuits_finished'], 
                                    arguments = arguments, 
                                    vqpu_id = vqpu_id,
                                    circuits = circuits
                                    ))
        if add_other_tasks:
            tg.create_task(cpuflow(arguments))
            tg.create_task(gpuflow(arguments))

    for k in events.keys():
        events[k].clean()

    logger.info("Finished hybrid (v)QPU workflow")

@flow 
def flow_wrapper_to_async_flow(arguments: str):
    '''
    @brief run the workflow with the appropriate task runner
    '''
    task_runners = get_dask_runners(cluster='ella-qb')

    asyncio.run(workflow(task_runners, arguments))

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
