"""
@file vqpuflow.py
@brief Collection of tasks and flows related to vqpu and circuits

"""

import time, datetime, subprocess, os, select, psutil, copy
import json
from pathlib import Path
import importlib
import numpy as np
from typing import List, Any, Dict, NamedTuple, Optional, Tuple, Union, Generator, Callable
from vqpucommon.clusters import get_dask_runners
from vqpucommon.utils import check_python_installation, save_artifact, run_a_srun_process, run_a_process, run_a_process_bg, get_task_run_id, get_job_info, get_flow_runs, upload_image_as_artifact, SlurmInfo, EventFile
from vqpucommon.vqpubase import HybridQuantumWorkflowBase, HybridQuantumWorkflowSerializer, SillyTestClass
import asyncio
from prefect import flow, task
from prefect.logging import get_run_logger
from prefect.artifacts import Artifact

@task(retries = 5, 
    retry_delay_seconds = 10, 
    timeout_seconds=600,
    task_run_name = 'Task-Launch-vQPU-{vqpu_id}',
    result_serializer=HybridQuantumWorkflowSerializer(),
    )
async def launch_vqpu(
    myqpuworkflow : HybridQuantumWorkflowBase,
    vqpu_id : int,
    spinuptime : float = 30, 
    arguments : str | None = None, 
    ) -> None:
    """Base task that launches the virtual qpu. 
    Should have minimal retries and a wait between retries
    once vqpu is launched set event so subsequent circuit tasks can run 

    Args:
        myqpuworkflow (HybridQuantumWorkflowBase) : hybrid workflow class that manages events, yamls, etc when launching vqpu
        vqpu_id (int): The vqpu id
        spinuptime (float): The delay time to sleep for representing how long it takes to spin-up the vqpu
        arguments (str): Arguments that could be used 
    """
    myqpuworkflow.checkbackends()
    myqpuworkflow.checkvqpuid(vqpu_id)

    # get flow run information and slurm job information 
    job_info = get_job_info()
    logger = get_run_logger()

    logger.info(f'Spinning up vQPU-{vqpu_id} backend')
    logger.info(f'vQPU-{vqpu_id}: setting config yamls')
    logger.info(f'vQPU-{vqpu_id}: running script {myqpuworkflow.vqpu_script}')
    await myqpuworkflow.launch_vqpu(job_info = job_info, vqpu_id = vqpu_id, spinuptime = spinuptime)
    logger.info(f'Running vQPU-{vqpu_id} ... ')


@task(retries = 0, 
    task_run_name = 'Task-Run-vQPU-{vqpu_id}',
    result_serializer=HybridQuantumWorkflowSerializer(),
    )
async def run_vqpu(
    myqpuworkflow : HybridQuantumWorkflowBase,
    vqpu_id : int,
    walltime : float = 86400, # one day
    ) -> None:
    """Runs a task that waits to keep flow active
    so long as there are circuits to be run or have not exceeded the walltime

    Args:
        myqpuworkflow (HybridQuantumWorkflowBase) : hybrid workflow class that manages events to run vqpu till shutdown signal generated
        vqpu_id (int): The vqpu id
        walltime (float): Walltime to wait before shutting down vqpu 
    """
    logger = get_run_logger()
    logger.info(f'vQPU-{vqpu_id} running and will keep running till circuits complete or hit walltime ... ')
    # generate a list of asyncio tasks to determine when to proceed and shutdown the vqpu
    tasks = [
        asyncio.create_task(myqpuworkflow.task_circuitcomplete(vqpu_id=vqpu_id)),
        asyncio.create_task(myqpuworkflow.task_walltime(walltime = walltime)),
    ]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    results = f"vQPU-{vqpu_id} can be shutdown. Reason : "
    for d in done:
        results += d.result()
    for remaining in pending:
        remaining.cancel()
    logger.info(results)

@task(retries = 5, 
    retry_delay_seconds = 2, 
    timeout_seconds=600,
    task_run_name = 'Task-Shutdown-vQPU-{vqpu_id}',
    result_serializer=HybridQuantumWorkflowSerializer(),
    )
async def shutdown_vqpu(
    myqpuworkflow : HybridQuantumWorkflowBase,
    vqpu_id : int,
    ) -> None:
    """Shuts down vqpu 

    Args:
        myqpuworkflow (HybridQuantumWorkflowBase) : hybrid workflow class that manages shutdown of vqpu
        vqpu_id (int): The vqpu id
    """
    logger = get_run_logger()
    logger.info(f'Shutdown vQPU-{vqpu_id} backend')
    myqpuworkflow.shutdown_vqpu(vqpu_id=vqpu_id)
    logger.info("vQPU(s) shutdown")

@flow(name = "Launch vQPU Flow", 
    flow_run_name = "launch_vqpu_{vqpu_id}_flow_on-{date:%Y-%m-%d:%H:%M:%S}",
    description = "Launching the vQPU only portion with the appropriate task runner", 
    retries = 3, 
    retry_delay_seconds = 10, 
    log_prints=True, 
    result_serializer=HybridQuantumWorkflowSerializer(),
    )
async def launch_vqpu_workflow(
    myqpuworkflow : HybridQuantumWorkflowBase,
    vqpu_id : int = 1,
    walltime : float = 86400, 
    arguments : str = "", 
    date : datetime.datetime = datetime.datetime.now() 
    ) -> None:
    """Flow for running vqpu 

    Args:
        myqpuworkflow (HybridQuantumWorkflowBase) : hybrid workflow class that manages vqpu workflow
        vqpu_id (int): vqpu id 
        walltime (float) : walltime to keep running the vqpu 
        arguments (str): string of arguments to pass extra options to launching of vqpu

    """
    # clean up before launch
    myqpuworkflow.cleanupbeforestart(vqpu_id=vqpu_id)
    logger = get_run_logger()

    # now launch
    logger.info(f'Launching vQPU-{vqpu_id}')
    future = await launch_vqpu.submit(myqpuworkflow = myqpuworkflow, vqpu_id = vqpu_id, arguments = arguments)
    await future.result()
    
    # now run it 
    logger.info(f'vQPU-{vqpu_id} running and will keep running till circuits complete or hit walltime ... ')
    future = await run_vqpu.submit(myqpuworkflow = myqpuworkflow, vqpu_id = vqpu_id, walltime = walltime)
    await future.result()

    # once the run has finished, shut it down
    future = await shutdown_vqpu.submit(myqpuworkflow = myqpuworkflow, vqpu_id = vqpu_id)
    await future.result()

async def postprocessing_histo_plot(
        data : Dict[str,int],
        arguments : str, 
        normalize : bool = True,
        cmapname : str = 'viridis',
    ) -> Dict[str, int] :
    """Simple post_processing of circuit, creating a histogram, 
    saving the file and also creating a markdown artifact for prefect
    This is a useful post processing step 

    Args:
        data (Dict[str,int]): bitstring data
        arguments (str): arguments that should contain the filename and title

    Returns:
        Dict[str,int]: Post-processed results. Here no processing is done
    """
    logger = get_run_logger()
    libcheck = check_python_installation('matplotlib')
    if libcheck == False:
        logger('Missing matplotlib, not producing histogram')
        return data
    import matplotlib.pyplot as plt
    filename = arguments.split('--filename=')[1].split(' ')[0]
    title = arguments.split('--plottitle=')[1].split(' ')[0]

    counts = np.array([data[k] for k in data.keys()], dtype=np.float32)
    if normalize: 
        counts /= np.sum(counts)
        maxnormval = 1
    else:
        maxnormval = np.sum(counts)

    fig, ax = plt.subplots(1, 1, tight_layout=True, figsize=(8,8))
    norm = plt.Normalize(vmin=0, vmax=maxnormval)
    cmap = plt.cm.get_cmap(cmapname)
    colors = cmap(norm(counts))
    # Create bars
    bars = ax.bar(data.keys(), counts, color=colors)
    ax.set_ylabel('Probability')
    ax.set_xlabel('States')
    ax.set_title(title)
    plt.savefig(f'{filename}.png')
    # save the figure so that it can be directly viewed from the Prefect UI
    await upload_image_as_artifact(Path(f'{filename}.png'))
    return data 

@task(retries = 10, 
    retry_delay_seconds = 2,
    retry_jitter_factor = 0.5,
    task_run_name = 'Run_circuit_vqpu-{circuitfunc.__name__}-vqpu-{vqpu_id}',
    result_serializer=HybridQuantumWorkflowSerializer(),
    )
async def run_circuit_vqpu(
    myqpuworkflow : HybridQuantumWorkflowBase,
    circuitfunc : Callable, 
    vqpu_id : int = 1,
    arguments : str = '',
    remote : str = '', 
    ) -> Tuple[Dict[str, int], int]:
    """Run a circuit on a given remote vqpu (or QB QPU) backend

    Args:
        myqpuworkflow (HybridQuantumWorkflowBase) : hybrid workflow class that manages running circuits on a vqpu
        circuitfunc (Callable) : function to run 
        vqpu_id (int): The vqpu id to use 
        arguments (str): string of arguments to pass to circuit function
        remote (str) : remote vQPU to use 

    Returns:
        Tuple[Dict[str, int], int]: Dictionary results from a circuit that consists of bitstrings and counts along with the id of this task running the circuit
    """
    myqpuworkflow.checkbackends(checktype='circuit')
    results = circuitfunc(remote, arguments)
    task_run_id = get_task_run_id()
    return results, task_run_id

@task(retries = 10, 
    retry_delay_seconds = 2,
    retry_jitter_factor = 0.5,
    task_run_name = 'Run_circuit_remote-{circuitfunc.__name__}',
    result_serializer=HybridQuantumWorkflowSerializer(),
    )
async def run_circuit_remote(
    myqpuworkflow : HybridQuantumWorkflowBase,
    circuitfunc : Callable, 
    arguments : str = '',
    remote : str = '', 
    ) -> Tuple[Dict[str, int], int]:
    """Run a circuit on a given remote backend. This requires the callable circuit to handle chatting to the appropriate backend and raising exceptions if necessary.

    Args:
        myqpuworkflow (HybridQuantumWorkflowBase) : hybrid workflow class that manages remote circuit workflow
        circuitfunc (Callable) : function to run 
        arguments (str): string of arguments to pass to circuit function
        remote (str) : remote vQPU to use 

    Returns:
        Tuple[Dict[str, int], int]: Dictionary results from a circuit that consists of bitstrings and counts along with the id of this task running the circuit
    """
    results = circuitfunc(remote, arguments)
    task_run_id = get_task_run_id()
    return results, task_run_id

@task(retries = 10, 
    retry_delay_seconds = 2,
    retry_jitter_factor = 0.5,
    task_run_name = 'Run_circuit_sim-{circuitfunc.__name__}-with-{backend_sel}',
    result_serializer=HybridQuantumWorkflowSerializer(),
    )
async def run_circuit_sim(
    myqpuworkflow : HybridQuantumWorkflowBase,
    circuitfunc : Callable,
    backend_sel : str,
    arguments : str = '',
    ) -> Tuple[Dict[str, int], int]:
    """Run a circuit simulation using a given backend like qiskit, cudaq, etc. Does not require a remote service running, like a QPU would have

    Args:
        myqpuworkflow (HybridQuantumWorkflowBase) : hybrid workflow class that manages circuit sim workflow
        circuitfunc (Callable) : function to run 
        backend_sel (str): Backend selection 
        arguments (str): string of arguments to pass to circuit function

    Returns:
        Tuple[Dict[str, int], int]: Dictionary results from a circuit that consists of bitstrings and counts along with the id of this task running the circuit
    """
    myqpuworkflow.checkbackends(backend = backend_sel, checktype= 'circuit')
    results = circuitfunc(arguments)
    task_run_id = get_task_run_id()
    return results, task_run_id

@task(retries = 10, 
    retry_delay_seconds = 2,
    retry_jitter_factor = 0.5,
    task_run_name = 'Run_circuit-{circuitfunc.__name__}',
    result_serializer=HybridQuantumWorkflowSerializer(),
    )
async def run_circuit(
    myqpuworkflow : HybridQuantumWorkflowBase,
    circuitfunc : Callable, 
    arguments : str | None = None,
    backend_sel : str  | None = None,
    vqpu_id : int | None = None,
    remote : str | None = None, 
    ) -> Tuple[Dict[str, int], int]:
    """Wrapper for running all the run circuit tasks, regardless of whether they run 
    on vqpus, simulation or remotes. Selection of task run depends on arguments passed. 

    Args:
        myqpuworkflow (HybridQuantumWorkflowBase) : hybrid workflow class that manages circuit workflow
        circuitfunc (Callable) : function to run 
        arguments (str): string of arguments to pass to circuit function
        backend_sel (str): Desired simulation backend to use. 
        vqpu_id (int): The vqpu id to use 
        remote (str) : remote (v)QPU to use 

    Returns:
        Tuple[Dict[str, int], int]: Dictionary results from a circuit that consists of bitstrings and counts along with the id of this task running the circuit
    """
    if backend_sel == None and vqpu_id != None and remote != None:
        future = await run_circuit_vqpu.submit(
            myqpuworkflow = myqpuworkflow,
            circuitfunc = circuitfunc,
            arguments = arguments,
            vqpu_id = vqpu_id, 
            remote = remote, 
        )
    elif backend_sel == None and vqpu_id == None and remote != None:
        future = await run_circuit_remote.submit(
            myqpuworkflow = myqpuworkflow,
            circuitfunc = circuitfunc,
            arguments = arguments,
            remote = remote, 
        )
    elif backend_sel != None and vqpu_id == None and remote == None:
        future = await run_circuit_sim.submit(
            myqpuworkflow = myqpuworkflow,
            circuitfunc = circuitfunc,
            arguments = arguments,
            backend_sel = backend_sel
        )
    else:
        message = 'Issues with running circuit.\n'
        message += 'Arguments must be either:\n'
        message += 'a (vqpu_id, remote) for running on vQPU\n'
        message += 'a (remote) for running on a QPU remote backend\n'
        message += 'a (backend_sel) for running a simulation using a given backend\n'
        message += 'Terminating.'
        raise RuntimeError(message)

    results, task_run_id = await future.result()
    return results, task_run_id

@task(retries = 10, 
    retry_delay_seconds = 2,
    retry_jitter_factor=0.5,
    task_run_name = 'Run_postprocess-{postprocessfunc.__name__}-circuit_id-{circuit_job_id}',
    result_serializer=HybridQuantumWorkflowSerializer(),
    )
async def run_postprocess(
    myqpuworkflow : HybridQuantumWorkflowBase,
    postprocessfunc : Callable, 
    initial_results : Dict[str,int],
    circuit_job_id : int, 
    arguments : str, 
    ) -> Tuple[Any, int]:
    """Run a small postprocessing

    Args:
        myqpuworkflow (HybridQuantumWorkflowBase) : hybrid workflow class that manages circuit workflow
        postprocessfunc (Callable) : function to run postprocessing results from a circuit
        initial_results (Dict[str,int]) : results from the circuit 
        circuit_job_id (int): The job id of the circuit producing the initial results 
        arguments (str): string of arguments to pass to circuit function

    Returns:
        Tuple[Any, int]: The postprocessing rsult and task running the postprocessing
    """
    # note that below since artifcate saves remote file name, don't need the below check and parsing
    results = await postprocessfunc(initial_results, arguments)
    task_run_id = get_task_run_id()
    return results, task_run_id


@task(retries = 10, 
    retry_delay_seconds = 2,
    retry_jitter_factor = 0.5,
    task_run_name = 'Run_circuitsandpost-vqpu-{vqpu_id}',
    result_serializer=HybridQuantumWorkflowSerializer(),
    )
async def run_circuitandpost_vqpu(
    myqpuworkflow : HybridQuantumWorkflowBase,
    circuit : Callable | Tuple[Callable, Callable],
    vqpu_id : int,
    arguments : str,
    remote : str,
    produce_histo : bool = True,
    ) -> Dict[str, Any]:
    """Run circuit (and possibly postprocessing) on a given remote backend

    Args:
        myqpuworkflow (HybridQuantumWorkflowBase) : hybrid workflow class that manages circuit workflow
        circuit (Callable | Tuple[Callable, Callabe]) : function(s) to run 
        vqpu_id (int): The vqpu id to use 
        arguments (str): string of arguments to pass to circuit function
        remote (str) : remote vQPU to use 
        produce_histo (bool) : produce a histogram of initial bitstrings

    Returns:
        Dict[str, Any]: Dictionary storing initial and postprocessed results
    """
    logger = get_run_logger()
    results = {'circuit': None, 'post' : None}
    circ, post = myqpuworkflow.getcircuitandpost(circuit)
    logger.info(f'Running {circ.__name__}')
    future = await run_circuit_vqpu.submit(
        circuitfunc = circ,
        arguments = arguments,
        vqpu_id = vqpu_id, 
        remote = remote, 
        )
    circ_results, circ_id = await future.result()
    results['circuit'] = {'results': circ_results, 'name': circ.__name__, 'id': circ_id}
    logger.debug(f'{circ.__name__} with {circ_id} results: {circ_results}')
    results['post'] = {'results': None, 'name': None, 'id': None}
    if post != None :
        future = await run_postprocess.submit(
            postprocessfunc = post, 
            initial_results = circ_results,
            circuit_job_id = circ_id, 
            arguments = arguments, 
        )
        post_results, post_id = await future.result()
        results['post'] = {'results': post_results, 'name': post.__name__, 'id': post_id}
        logger.debug(f'Results from postprocessing {post.__name__} : {post_results}')    
    elif produce_histo:
        postprocess_args = f' --filename=plots/{circ.__name__}.vqpu-{vqpu_id}.{circ_id}.histo --plottitle=testing '
        future = await run_postprocess.submit(
            postprocessfunc = postprocessing_histo_plot, 
            initial_results = circ_results,
            circuit_job_id = circ_id, 
            arguments = postprocess_args, 
        )
        post_results, post_id = await future.result()
        results['post'] = {'results': post_results, 'name': post.__name__, 'id': post_id}
        logger.debug(f'Results from postprocessing {post.__name__} : {post_results}')

    return results

@task(
    retries = 5,
    retry_delay_seconds = 0.5,
    retry_jitter_factor = 0.5,
    task_run_name = 'Run_circuits_when-vqpu-{vqpu_id}-ready',
    result_serializer=HybridQuantumWorkflowSerializer(),
)
async def run_circuits_once_vqpu_ready(
    myqpuworkflow : HybridQuantumWorkflowBase,
    circuits : Dict[str, List[Callable | Tuple[Callable, Callable]]],
    vqpu_id : int,  
    arguments : str, 
    circuits_complete : bool = True, 
    ):
    """Run set of circuits (and possibly post processing) once the appropriate vqpu is ready

    Args:
        myqpuworkflow (HybridQuantumWorkflowBase) : hybrid workflow class that manages circuit workflow
        circuits (Dict[str, List[Callable | Tuple[Callable, Callable]]]): circuits (and postprocessing) to run for a given vqpu 
        vqpu_id (int): vqpu id on which to run circuits
        arguments (str): string of arguments to pass extra options to running circuits and postprocessing
        circuits_complete (bool) : Whether to trigger the circuits complete shutdown of vqpu
    """
    results = list()
    logger = get_run_logger()
    key = f'vqpu_{vqpu_id}'
    remote = await myqpuworkflow.getremoteafterlaunch(vqpu_id = vqpu_id)
    logger.info(f'{key} running, submitting circuits ...')
    for c in circuits[key]:
        result = await run_circuitandpost_vqpu.fn(myqpuworkflow=myqpuworkflow, circuit=c, vqpu_id=vqpu_id, arguments=arguments, remote=remote)
        results.append(result)
    # if circuits completed should trigger a shutdown of the vqpu, then set the circuits complete event
    if circuits_complete: myqpuworkflow.events[f'vqpu_{vqpu_id}_circuits_finished'].set()
    return results

@flow(name = "Circuits flow", 
    flow_run_name = "circuits_flow_for_vqpu_{vqpu_id}_on-{date:%Y-%m-%d:%H:%M:%S}",
    description = "Running circutis on the vQPU with the appropriate task runner for launching circuits", 
    retries = 3, 
    retry_delay_seconds = 20, 
    log_prints = True,
    result_serializer=HybridQuantumWorkflowSerializer(),
    )
async def circuits_vqpu_workflow(
    myqpuworkflow : HybridQuantumWorkflowBase,
    circuits : List[Callable | Tuple[Callable, Callable]],
    arguments : str = "", 
    vqpu_id : int = 1,  
    delay_before_start : float = 10.0, 
    circuits_complete : bool = True, 
    date : datetime.datetime = datetime.datetime.now() 
    ) -> None:
    """Flow for running circuits (and any postprocessing)

    Args:
        myqpuworkflow (HybridQuantumWorkflowBase): hybrid workflow class that manages circuit workflow
        circuits (List[Callable | Tuple[Callable, Callable]]): circuits (and postprocessing) to run
        vqpu_id (int): vqpu id 
        arguments (str): string of arguments to pass extra options to launching of vqpu
        delay_before_start (float): how much time to wait before running circuits
        circuits_complete (bool): whether to issue circuits complete vqpu shutdown signal 

    """
    if (delay_before_start < 0): delay_before_start = 0
    # clean-up any events
    myqpuworkflow.cleanupbeforestart() 
    logger = get_run_logger()
    logger.info(f'Delay of {delay_before_start} seconds before starting circuit submission ... ')
    await asyncio.sleep(delay_before_start)
    logger.info(f'Waiting for vqpu-{vqpu_id} to start ... ')
    remote = myqpuworkflow.getremoteaftervqpulaunch(vqpu_id=vqpu_id)
    
    logger.info(f'vqpu-{vqpu_id} running, submitting circuits ...')
    results = list()
    for c in circuits:
        result = await run_circuitandpost_vqpu.fn(myqpuworkflow=myqpuworkflow, circuit=c, vqpu_id=vqpu_id, arguments=arguments, remote=remote)
        results.append(result)
    logger.info('Finished all running all circuits')
    if circuits_complete: myqpuworkflow.events[f'vqpu_{vqpu_id}_circuits_finished'].set()
    return results

@flow(name = "Circuits with multi-vQPU, GPU flow", 
    flow_run_name = "circuits_flow_for_vqpu_{vqpu_ids}_on-{date:%Y-%m-%d:%H:%M:%S}",
    description = "Running circutis on the vQPU with the appropriate task runner for launching circuits and also launches a gpu workflow, ", 
    retries = 3, 
    retry_delay_seconds = 20, 
    log_prints = True,
    result_serializer=HybridQuantumWorkflowSerializer(),
    )
async def circuits_with_nqvpuqs_workflow(
    myqpuworkflow : HybridQuantumWorkflowBase,
    circuits : Dict[str, List[Callable | Tuple[Callable, Callable]]],
    arguments : str, 
    delay_before_start : float = 10.0,
    circuits_complete : bool = True, 
    date : datetime.datetime = datetime.datetime.now() 
    ) -> None:
    """Example flow for running circuits (and any postprocessing) on multiple vqpus

    Args:
        myqpuworkflow (HybridQuantumWorkflowBase): hybrid workflow class that manages circuit workflow
        circuits (Dict[str, List[Callable | Tuple[Callable, Callable]]]): circuits (and postprocessing) to run for a given vqpu 
        arguments (str): string of arguments to pass extra options to running circuits and postprocessing
        delay_before_start (float) : seconds to wait before trying to run circuits 
        circuits_complete (bool) : Whether to trigger the circuits complete shutdown of vqpu
    """

    logger = get_run_logger()

    if (delay_before_start < 0): delay_before_start = 0
    myqpuworkflow.cleanupbeforestart()

    # run delay 
    if delay_before_start > 0:
        logger.info(f'Delay of {delay_before_start} seconds before starting ... ')
        await asyncio.sleep(delay_before_start)

    logger.info(f'Waiting for vqpu-{myqpuworkflow.vqpu_ids} to start ... ')

    # due to task runners, serializing the run_circuits_once_vqpu_ready
    # task cannot be submitted using future = await run_circuits_once_vqpu_ready.submit() it appears
    # but an easy option is to use a task group 
    tasks = dict()
    async with asyncio.TaskGroup() as tg:
        # either spin up real vqpu
        for vqpu_id in myqpuworkflow.vqpu_ids:
            tasks[vqpu_id] = tg.create_task(run_circuits_once_vqpu_ready(
                myqpuworkflow=myqpuworkflow,
                circuits = circuits,
                vqpu_id = vqpu_id, 
                arguments = arguments, 
                circuits_complete=circuits_complete,
                )
            )
    results = {f'vqpu-{name}': task.result() for name, task in tasks.items()}
    logger.debug(results)
    return results

def run_workflow_circuits_with_nqvpuqs(
    myqpuworkflow : HybridQuantumWorkflowBase,
    circuits : Dict[str, List[Callable | Tuple[Callable, Callable]]],
    arguments : str, 
    delay_before_start : float = 10.0,
    circuits_complete : bool = True, 
    ) -> None:
    """Wrapper that ensures running the associated flow with correct task runner 

    Args:
        myqpuworkflow (HybridQuantumWorkflowBase): hybrid workflow class that manages circuit workflow
        circuits (Dict[str, List[Callable | Tuple[Callable, Callable]]]): circuits (and postprocessing) to run for a given vqpu 
        arguments (str): string of arguments to pass extra options to running circuits and postprocessing
        delay_before_start (float) : seconds to wait before trying to run circuits 
        circuits_complete (bool) : Whether to trigger the circuits complete shutdown of vqpu
    """
    my_flow = circuits_with_nqvpuqs_workflow.with_options(
        task_runner = myqpuworkflow.gettaskrunners('circuit'),
    )
    asyncio.run(my_flow(circuits, arguments, delay_before_start, circuits_complete))

@task(retries = 10, 
    retry_delay_seconds = 2,
    timeout_seconds=3600,
    task_run_name = 'Run_cpu_{exec}',
    result_serializer=HybridQuantumWorkflowSerializer(),
    )
async def run_cpu(
    exec : str, 
    arguments: str,
    myqpuworkflow : HybridQuantumWorkflowBase | None = None
    ) -> None:
    """Running CPU based programs. 

    Args:
        exec (str) : executable to run as a process
        arguments (str): string of arguments to pass extra options to run cpu 
    """
    logger = get_run_logger()
    logger.info('Launching CPU task')
    cmds = [exec]
    if ','in arguments:
        cmds += arguments.split(',')
    else:
        cmds.append(arguments)
    logger.info(cmds)
    process = run_a_process(cmds)
    logger.info("Finished CPU task")

@task(retries = 10, 
    retry_delay_seconds = 2,
    timeout_seconds=3600,
    task_run_name = 'Run_gpu_{exec}',
    result_serializer=HybridQuantumWorkflowSerializer(),
    )
async def run_gpu(
    exec: str, 
    arguments: str,
    myqpuworkflow : HybridQuantumWorkflowBase | None = None,
    ) -> None:
    """Running GPU based programs. 

    Args:
        exec (str) : executable to run as a process
        arguments (str): string of arguments to pass extra options to run cpu 
    """
    logger = get_run_logger()
    logger.info("Launching GPU task")
    cmds = [exec]
    if ','in arguments:
        cmds += arguments.split(',')
    else:
        cmds.append(arguments)
    logger.info(cmds)
    process = run_a_process(cmds)
    logger.info("Finished GPU task")

@flow(name = "Simple CPU flow", 
    flow_run_name = "cpu_flow_on-{date:%Y-%m-%d:%H:%M:%S}",
    description = "Running CPU flows", 
    retries = 3, 
    retry_delay_seconds = 10, 
    log_prints=True, 
    result_serializer=HybridQuantumWorkflowSerializer(),
    )
async def cpu_workflow(
    execs: List[str], 
    arguments : List[str],
    myqpuworkflow : HybridQuantumWorkflowBase | None = None,
    date : datetime.datetime = datetime.datetime.now() 
    ) -> None:
    """Flow for running cpu based programs.

    Args:
        execs (List[str]): List of execs to run in a CPU oriented DaskTaskScheduler 
        arguments (List[str]): List of corresponding arguments 
    """
    logger = get_run_logger()
    logger.info("Launching CPU flow")
    # submit the task and wait for results
    futures = []
    for exec, args in zip(execs, arguments):
        futures.append(await run_cpu.submit(myqpuworkflow=myqpuworkflow, exec = exec, arguments = args))
    for f in futures:
        await f.result()
    
    logger.info("Finished CPU flow")

@flow(name = "Simple GPU flow", 
    flow_run_name = "gpu_flow_on-{date:%Y-%m-%d:%H:%M:%S}",
    description = "Running GPU flows", 
    retries = 3, 
    retry_delay_seconds = 10, 
    log_prints=True, 
    result_serializer=HybridQuantumWorkflowSerializer(),
    )
async def gpu_workflow(
    execs: List[str], 
    arguments : List[str],
    myqpuworkflow : HybridQuantumWorkflowBase | None = None,
    date : datetime.datetime = datetime.datetime.now(),
    ) -> None:
    """Flow for running GPU based programs.

    Args:
        execs (List[str]): List of execs to run in a GPU oriented DaskTaskScheduler 
        arguments (List[str]): List of corresponding arguments 
    """
    logger = get_run_logger()
    logger.info("Launching GPU flow")
    # submit the task and wait for results
    futures = []
    for exec, args in zip(execs, arguments):
        futures.append(await run_gpu.submit(myqpuworkflow=myqpuworkflow, exec=exec, arguments=args))
    for f in futures:
        await f.result()
    logger.info("Finished GPU flow")

def run_workflow_cpu(
    myqpuworkflow : HybridQuantumWorkflowBase,
    execs: List[str], 
    arguments : List[str],
    ) -> None:
    """Wrapper to run flow for CPU programs with appropriate task runner.

    Args:
        execs (List[str]): List of execs to run in a CPU oriented DaskTaskScheduler 
        arguments (List[str]): List of corresponding arguments 
    """
    my_flow = cpu_workflow.with_options(
        task_runner = myqpuworkflow.gettaskrunner('cpu'),
    )
    asyncio.run(my_flow(myqpuworkflow=myqpuworkflow, execs=execs, arguments=arguments))

def run_workflow_gpu(
    myqpuworkflow : HybridQuantumWorkflowBase,
    execs: List[str], 
    arguments : List[str],
    ) -> None:
    """Wrapper to run flow for GPU programs with appropriate task runner.

    Args:
        execs (List[str]): List of execs to run in a GPU oriented DaskTaskScheduler 
        arguments (List[str]): List of corresponding arguments 
    """
    my_flow = gpu_workflow.with_options(
        task_runner = myqpuworkflow.gettaskrunner('gpu'),
    )
    asyncio.run(my_flow(myqpuworkflow=myqpuworkflow, execs=execs, arguments=arguments))

    
@task()
def TaskForSillyTestClass(obj1 : SillyTestClass, obj2 : SillyTestClass):
    obj1.x += 2
    obj2.x -= 1
    obj1.y = 1
    obj2.y = 2

@flow()
def FlowForSillyTestClass(baseobj : SillyTestClass | None = None):
    if baseobj != None:
        baseobj.x = baseobj.y
    obj1 = SillyTestClass(x=100)
    obj2 = SillyTestClass(x=0)
    future = TaskForSillyTestClass.submit(obj1 = obj1, obj2 = obj2)
    future.result()
