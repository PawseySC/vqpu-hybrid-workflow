"""
@file vqpuworkflow.py
@brief Collection of tasks and flows related to vqpu and circuits

"""

import time, datetime, subprocess, os, select, psutil
from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np
from typing import List, Any, Dict, NamedTuple, Optional, Tuple, Union, Generator, Callable
from vqpucommon.clusters import get_dask_runners
from vqpucommon.utils import save_artifact, run_a_srun_process, run_a_process, run_a_process_bg, get_job_info, get_flow_runs, upload_image_as_artifact, SlurmInfo, EventFile
import asyncio
from prefect_dask import DaskTaskRunner
from prefect import flow, task, get_client, pause_flow_run
from prefect.logging import get_run_logger
from prefect.artifacts import Artifact
from prefect.context import get_run_context

class HybridQuantumWorkflowBase:
    """
    A class that contains basic tasks for running hybrid vQPU workflows.
    """

    cluster: str
    """cluster name for slurm configurations"""
    maxvqpu: int
    """max number of virtual qpus"""
    vqpu_script : str =  f'{os.path.dirname(os.path.abspath(__file__))}/../qb-vqpu/vqpu.sh'
    """vqpu start up script to run"""
    vqpu_temmplate_yaml : str = f'{os.path.dirname(os.path.abspath(__file__))}/remote_vqpu_template.yaml'
    """vqpu remote yaml template"""
    vqpu_yaml_dir : str = f'{os.path.dirname(os.path.abspath(__file__))}/../vqpus/'
    """directory where to store the active vqpu yamls"""
    vqpu_exec : str = 'qcstack'
    """vqpu executable. Default is QB's vQPU executable"""
    vqpu_ids : List[int]
    """list of active vqpus"""
    events : Dict[str, EventFile]
    """list of events"""
    eventloc : str
    """location of where to store event files"""
    taskrunners : Dict[str, DaskTaskRunner | Dict[str,str]]
    """The slurm configuration stored in the DaskTaskRunner used by Prefect"""
    backends : List[str]
    """List of allowed backends"""

    def __init__(self, cluster : str, 
                 vqpu_ids : List[int] = [1], 
                 backends : List[str] = ['qb-vqpu', 'braket', 'quera', 'qiskit'], 
                 eventloc : str = './events/', 
                 vqpu_script : str | None = None, 
                 vqpu_template_yaml : str | None = None, 
                 ):
        """
        Constructs all the necessary attributes for the person object.

        Args:
            cluster (str): cluster name.
            maxvqpu (int): max number of vqpus to launch
        """

        self.cluster = cluster
        self.taskrunners = get_dask_runners(cluster=self.cluster)
        self.backends = backends
        self.vqpu_ids = vqpu_ids
        self.eventloc = eventloc
        if 'qb-vqpu' in backends:
            if vqpu_script != None:
                self.vqpu_script = vqpu_script
            if vqpu_template_yaml != None:
                self.vqpu_temmplate_yaml = vqpu_template_yaml
            for vqpu_id in self.vqpu_ids:
                self.events[f'vqpu_{vqpu_id}_launch'] = EventFile(name = f'vqpu_{vqpu_id}_launch', loc = self.eventloc) 
                self.events[f'vqpu_{vqpu_id}_circuits_finished'] = EventFile(name = f'vqpu_{vqpu_id}_circuits_finished', loc = self.eventloc)

    def report_config(self):
        """
        Report config of the hybrid workflow
        """
        logger = get_run_logger()
        logger.info('Hybrid Quantum Workflow running on ')
        logger.info(f'Cluster : {self.cluster}')
        for key, value in self.taskrunners['jobscript'].items():
            logger.info(f'Slurm configuration {key}: {value}')
        logger.info(f'Allowed QPU backends : {self.backends}')

    async def __create_vqpu_remote_yaml(
        self, 
        job_info : Union[SlurmInfo], 
        vqpu_id : int 
        ) -> None:
        """Saves the remote backend for the vqpu to a yaml file and artifact
        having extracted the hostname running the vqpu from the slurm job

        Args:
            job_info (Union[SlurmInfo]): slurm job information 
            vqpu_id (int): The vqpu id
        """
        # where to save the workflow yaml
        workflow_yaml = f'{self.vqpu_yaml_dir}/remote_vqpu-{vqpu_id}.yaml'
        # here there is an assumption of the path to the template
        lines = open(self.vqpu_temmplate_yaml, 'r').readlines()
        fout = open(workflow_yaml, 'w')
        # update the HOSTNAME 
        for line in lines:
            if 'HOSTNAME' in line:
                line = line.replace('HOSTNAME', job_info.hostname)
            fout.write(line)
        # to store the results of this task, make use of a helper function that creates artifcat 
        await save_artifact(workflow_yaml, key=f'remote{vqpu_id}')
        await save_artifact(job_info.job_id, key=f'vqpujobid{vqpu_id}')
        
    def __delete_vqpu_remote_yaml(
        self, 
        vqpu_id : int, 
        ) -> None:
        """Removes the yaml file of the vqpu

        Args:
            vqpu_id (int): The vqpu id
        """

        workflow_yaml = f'{self.vqpu_yaml_dir}/remote_vqpu-{vqpu_id}.yaml'
        if os.path.isfile(workflow_yaml):
            os.remove(workflow_yaml)
    
    @task(retries = 5, 
        retry_delay_seconds = 10, 
        timeout_seconds=600,
        task_run_name = 'Task-Launch-vQPU-{vqpu_id}',
        )
    async def launch_vqpu(
        self,
        vqpu_id : int,
        spinuptime : float = 30, 
        arguments : str | None = None, 
        ) -> None:
        """Base task that launches the virtual qpu. 
        Should have minimal retries and a wait between retries
        once vqpu is launched set event so subsequent circuit tasks can run 

        Args:
            event (EventFile): The event file that is used to mark vqpu has started
            vqpu_id (int): The vqpu id
            spinuptime (float): The delay time to sleep for representing how long it takes to spin-up the vqpu
            arguments (str): Arguments that could be used 
        """
        if 'qb-vqpu' not in self.backends: 
            raise RuntimeError("vQPU requested to be launched yet qb-vqpu not in list of acceptable backends. Terminating")
        if vqpu_id not in self.vqpu_ids:
            raise RuntimeError(f"vQPU ID {vqpu_id} requested yet not in allowed list of ids. Terminating")


        # get flow run information and slurm job information 
        job_info = get_job_info()
        logger = get_run_logger()
        logger.info(f'Spinning up vQPU-{vqpu_id} backend')
        logger.info(f'vQPU-{vqpu_id}: setting config yamls')
        await self.__create_vqpu_remote_yaml(job_info, vqpu_id=vqpu_id)
        logger.info(f'vQPU-{vqpu_id}: running script {self.vqpu_script}')
        cmds = [self.vqpu_script]
        process = run_a_process(cmds)
        await asyncio.sleep(spinuptime)
        logger.info(f'vQPU-{vqpu_id}: script running ... ')
        # read the output of the process in a non-blocking manner 
        self.events[f'vqpu_{vqpu_id}_launch'].set()
        logger.info(f'Running vQPU-{vqpu_id} ... ')

    async def task_circuitcomplete(
            self,
            vqpu_id : int
            ) -> str:
        """Async task that indicates why vqpu can proceed to be shutdown as all circuits have been run

        Args:
            vqpu_id (int): The vqpu id

        Returns: 
            returns a message of what has completed 
        """
        await self.events[f'vqpu_{vqpu_id}_circuits_finished'].wait()
        return "Circuits completed!"

    async def task_walltime(walltime : float, vqpu_id : int) -> str:
        """Async task that indicates why vqpu can proceed to be shutdown as has exceeded walltime

        Args:
            walltime (float): Walltime to wait before shutting down vqpu 
            vqpu_id (int): The vqpu id
        Returns: 
            returns a message of what has completed 
        """
        await asyncio.sleep(walltime)
        return 'Walltime complete'

    @task(retries = 0, 
        task_run_name = 'Task-Run-vQPU-{vqpu_id}',
        )
    async def run_vqpu(
        self, 
        vqpu_id : int,
        walltime : float = 86400, # one day
        ) -> None:
        """Runs a task that waits to keep flow active
        so long as there are circuits to be run or have not exceeded the walltime

        Args:
            event (EventFile): The event file that is used to mark circuits have completed 
            vqpu_id (int): The vqpu id
            walltime (float): Walltime to wait before shutting down vqpu 
        """
        logger = get_run_logger()
        logger.info(f'vQPU-{vqpu_id} running and will keep running till circuits complete or hit walltime ... ')
        # generate a list of asyncio tasks to determine when to proceed and shutdown the vqpu
        tasks = [
            asyncio.create_task(self.task_circuitcomplete(self.events[f'vqpu_{vqpu_id}_circuits_finished'], vqpu_id=vqpu_id)),
            asyncio.create_task(self.task_walltime(walltime, vqpu_id=vqpu_id)),
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
        )
    async def shutdown_vqpu(
        self, 
        vqpu_id : int,
        ) -> None:
        """Shuts down vqpu 

        Args:
            vqpu_id (int): The vqpu id
        """
        logger = get_run_logger()
        logger.info(f'Shutdown vQPU-{vqpu_id} backend')
        self.__delete_vqpu_remote_yaml(vqpu_id)
        for proc in psutil.process_iter():
            # check whether the process name matches
            if proc.name() == self.vqpu_exec:
                proc.kill()
        logger.info("vQPU(s) shutdown")
    
    def __getcircuitandpost(
            self, 
            c : Any) -> Tuple:
        """process a possible circuit and any post processing to do 

        Args:
            c : possible combintation of circuit and post or just circuit
        """

        if not (isinstance(c, Callable) or isinstance(c, Tuple)):
            errmessage : str = "circuits argument was passed a List not containing either a callable function"
            errmessage += " or a tuple of callable fuctions consisting of a circuit and some post processing"
            errmessage += f"got type {type(c)}"
            raise ValueError(errmessage)
        # if the list contains a tuple then the circuit is 
        # followed by some post processing
        post = None
        circ = c
        if isinstance(c, Tuple) :
            # unpack the tuple and store the postprocessing call, p
            circ, post = c
            if not isinstance(circ, Callable) and not isinstance(post, Callable):
                errmessage : str = "circuits argument was passed a Tuple that did not consist of two callable functions"
                errmessage += " a circuit and some post processing"
                errmessage += f"got type {type(circ)} and {type(post)}"
                raise ValueError(errmessage)
        return circ, post

    async def postprocessing_histo_plot(
            self, 
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
        task_run_name = 'Run_circuit-{circuitfunc.__name__}-vqpu-{vqpu_id}',
        )
    async def run_circuit(
        self, 
        circuitfunc : Callable, 
        vqpu_id : int = 1,
        arguments : str = '',
        remote : str = '', 
        ) -> Tuple[Dict[str, int], int]:
        """Run a circuit on a given remote backend

        Args:
            circuitfunc (Callable) : function to run 
            vqpu_id (int): The vqpu id to use 
            arguments (str): string of arguments to pass to circuit function
            remote (str) : remote vQPU to use 

        Returns:
            Tuple[Dict[str, int], int]: Dictionary results from a circuit that consists of bitstrings and counts along with the id of this task running the circuit
        """
        # note that below since artifcate saves remote file name, don't need the below check and parsing
        results = circuitfunc(remote, arguments)
        context = get_run_context()
        task_run_id = context.task_run.id
        return results, task_run_id

    @task(retries = 10, 
        retry_delay_seconds = 2,
        retry_jitter_factor=0.5,
        task_run_name = 'Run_postprocess-{postprocessfunc.__name__}-circuit_id-{circuit_job_id}',
        )
    async def run_postprocess(
        self, 
        postprocessfunc : Callable, 
        initial_results : Dict[str,int],
        circuit_job_id : int, 
        arguments : str, 
        ) -> Tuple[Any, int]:
        """Run a small postprocessing

        Args:
            postprocessfunc (Callable) : function to run postprocessing results from a circuit
            initial_results (Dict[str,int]) : results from the circuit 
            circuit_job_id (int): The job id of the circuit producing the initial results 
            arguments (str): string of arguments to pass to circuit function

        Returns:
            Tuple[Any, int]: The postprocessing rsult and task running the postprocessing
        """
        # note that below since artifcate saves remote file name, don't need the below check and parsing
        results = await postprocessfunc(initial_results, arguments)
        context = get_run_context()
        task_run_id = context.task_run.id
        return results, task_run_id

    @task(retries = 10, 
        retry_delay_seconds = 2,
        retry_jitter_factor = 0.5,
        task_run_name = 'Run_circuitsandpost-vqpu-{vqpu_id}',
        )
    async def run_circuitandpost(
        self, 
        circuit : Callable | Tuple[Callable, Callable],
        vqpu_id : int,
        arguments : str,
        remote : str,
        produce_histo : bool = True,
        ) -> Dict[str, Any]:
        """Run circuit (and possibly postprocessing) on a given remote backend

        Args:
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
        circ, post = getcircuitandpost(circuit)
        logger.info(f'Running {circ.__name__}')
        future = await run_circuit.submit(
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
            postprocess_args = f' --filename=plots/{circ.__name__}.vqpu-{vqpu_id}.{circ_id}.histo --plottitle=testing '
            future = await run_postprocess.submit(
                postprocessfunc = post, 
                initial_results = circ_results,
                circuit_job_id = circ_id, 
                arguments = postprocess_args, 
            )
            post_results, post_id = await future.result()
            results['post'] = {'results': post_results, 'name': post.__name__, 'id': post_id}
            logger.debug(f'Results from postprocessing {post.__name__} : {post_results}')
        return results

    @task(
        task_run_name = 'clean_circuit_event_files-{vqpu_id}',            
    )
    def circuit_event_clean(
        events : Dict[str,EventFile],
        vqpu_ids : List[int]
        ) -> None:
        """Clean circuit finished event files. Useful to do on start-up to ensure proper event file creation

        Args:
            events (Dict[str,EventFile]) : event files 
            vqpu_ids (List[int]): list of vqpu ids
        """
        for vqpu_id in vqpu_ids:
            key = f'vqpu_{vqpu_id}_circuits_finished'
            events[key].clean()

    @task(
        task_run_name = 'Run_circuits_when-vqpu-{vqpu_id}-ready',
    )
    async def run_circuits_once_vqpu_ready(
        self,
        circuits : Dict[str, List[Callable | Tuple[Callable, Callable]]],
        vqpu_id : int,  
        arguments : str, 
        circuits_complete : bool = True, 
        ):
        """Run set of circuits (and possibly post processing) once the appropriate vqpu is ready

        Args:
            circuits (Dict[str, List[Callable | Tuple[Callable, Callable]]]): circuits (and postprocessing) to run for a given vqpu 
            vqpu_id (int): vqpu id on which to run circuits
            arguments (str): string of arguments to pass extra options to running circuits and postprocessing
            circuits_complete (bool) : Whether to trigger the circuits complete shutdown of vqpu
        """
        results = list()
        logger = get_run_logger()
        key = f'vqpu_{vqpu_id}'
        await self.events[key+'_launch'].wait()
        artifact = await Artifact.get(key=f'remote{vqpu_id}')
        remote = dict(artifact)['data'].split('\n')[1]
        logger.info(f'vqpu-{vqpu_id} running, submitting circuits ...')
        for c in circuits[key]:
            result = await run_circuitandpost.fn(circuit=c, vqpu_id=vqpu_id, arguments=arguments, remote=remote)
            results.append(result)
        # if circuits completed should trigger a shutdown of the vqpu, then set the circuits complete event
        if circuits_complete: self.events[key+'_circuits_finished'].set()
        return results


    @flow(name = "Circuits with multi-vQPU, GPU flow", 
        flow_run_name = "circuits_flow_for_vqpu_{vqpu_ids}_on-{date:%Y-%m-%d:%H:%M:%S}",
        description = "Running circutis on the vQPU with the appropriate task runner for launching circuits and also launches a gpu workflow, ", 
        retries = 3, 
        retry_delay_seconds = 20, 
        log_prints = True,
        )
    async def circuits_with_nqvpuqs_workflow(
        self, 
        circuits : Dict[str, List[Callable | Tuple[Callable, Callable]]],
        arguments : str, 
        delay_before_start : float = 10.0,
        circuits_complete : bool = True, 
        date : datetime.datetime = datetime.datetime.now() 
        ) -> None:
        """Example flow for running circuits (and any postprocessing) on multiple vqpus

        Args:
            circuits (Dict[str, List[Callable | Tuple[Callable, Callable]]]): circuits (and postprocessing) to run for a given vqpu 
            arguments (str): string of arguments to pass extra options to running circuits and postprocessing
            delay_before_start (float) : seconds to wait before trying to run circuits 
            circuits_complete (bool) : Whether to trigger the circuits complete shutdown of vqpu
        """

        logger = get_run_logger()

        if (delay_before_start < 0): delay_before_start = 0
        # clean-up any events 
        for vqpu_id in self.vqpu_ids:
            key = f'vqpu_{vqpu_id}_circuits_finished'
            self.events[key].clean()

        # run delay 
        if delay_before_start > 0:
            logger.info(f'Delay of {delay_before_start} seconds before starting ... ')
            await asyncio.sleep(delay_before_start)

        logger.info(f'Waiting for vqpu-{self.vqpu_ids} to start ... ')

        # due to task runners, serializing the run_circuits_once_vqpu_ready
        # task cannot be submitted using future = await run_circuits_once_vqpu_ready.submit() it appears
        # but an easy option is to use a task group 
        tasks = dict()
        async with asyncio.TaskGroup() as tg:
            # either spin up real vqpu
            for vqpu_id in self.vqpu_ids:
                tasks[vqpu_id] = tg.create_task(run_circuits_once_vqpu_ready(
                    circuits = circuits,
                    vqpu_id = vqpu_id, 
                    arguments = arguments, 
                    circuits_complete=circuits_complete,
                    )
                )
        results = {f'vqpu-{name}': task.result() for name, task in tasks.items()}
        logger.debug(results)
        return results


class MyHybridWorkflow(HybridQuantumWorkflowBase): 
    """
    Example class inheriting the basic workflow tasks for running vQPU workflows
    """


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

def test_circuit(
        remote : str, 
        arguments : str
        ) -> Dict[str, int]:
    """Simple test circuit for checking if prefect works. Provides standard interface
    This may be abstracted to a class

    Args:
        remote (str): remote qpu config, unused.
        arguments (str): arguments, unused by keeping interface the same
    Returns:
        Dict[str,int]: Post-processed results. Here no processing is done
    """
    
    results = {'000' : 0}
    return results

async def create_vqpu_remote_yaml(
    job_info : Union[SlurmInfo], 
    vqpu_id : int , 
    template_fname : str = f'{os.path.dirname(os.path.abspath(__file__))}/remote_vqpu_template.yaml'
    ) -> None:
    """Saves the remote backend for the vqpu to a yaml file and artifact
    having extracted the hostname running the vqpu from the slurm job

    Args:
        job_info (Union[SlurmInfo]): slurm job information 
        vqpu_id (int): The vqpu id
        template_fname (str) : The file name of the template to use 
    """
    # where to save the workflow yaml
    workflow_yaml = f'vqpus/remote_vqpu-{vqpu_id}.yaml'
    # here there is an assumption of the path to the template
    lines = open(template_fname, 'r').readlines()
    fout = open(workflow_yaml, 'w')
    # update the HOSTNAME 
    for line in lines:
        if 'HOSTNAME' in line:
            line = line.replace('HOSTNAME', job_info.hostname)
        fout.write(line)
    # to store the results of this task, make use of a helper function that creates artifcat 
    await save_artifact(workflow_yaml, key=f'remote{vqpu_id}')
    await save_artifact(job_info.job_id, key=f'vqpujobid{vqpu_id}')

def delete_vqpu_remote_yaml(
    vqpu_id : int, 
    ) -> None:
    """Removes the yaml file of the vqpu

    Args:
        vqpu_id (int): The vqpu id
    """

    workflow_yaml = f'vqpus/remote_vqpu-{vqpu_id}.yaml'
    if os.path.isfile(workflow_yaml):
        os.remove(workflow_yaml)

@task(retries = 5, 
      retry_delay_seconds = 10, 
      timeout_seconds=600,
      task_run_name = 'Task-Launch-vQPU-{vqpu_id}',
      )
async def launch_vqpu(
    event: EventFile, 
    vqpu_id : int,
    path_to_vqpu_script : str = os.path.dirname(os.path.abspath(__file__))+'/../qb-vqpu/', 
    spinuptime : float = 30, 
    arguments : str | None = None, 
    ) -> None:
    """Base task that launches the virtual qpu. 
    Should have minimal retries and a wait between retries
    once vqpu is launched set event so subsequent circuit tasks can run 

    Args:
        event (EventFile): The event file that is used to mark vqpu has started
        vqpu_id (int): The vqpu id
        path_to_vqpu_script (str): path where the vqpu start-up script is stored
        spinuptime (float): The delay time to sleep for representing how long it takes to spin-up the vqpu
        arguments (str): Arguments that could be used 
    """
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
    arguments: str | None = None, 
    ) -> None:
    """Base task that launches the virtual qpu. 
    Should have minimal retries and a wait between retries
    once vqpu is launched set event so subsequent circuit tasks can run 

    Args:
        event (EventFile): The event file that is used to mark vqpu has started
        vqpu_id (int): The vqpu id
        arguments (str): Arguments that could be used 
    """
    job_info = get_job_info()
    logger = get_run_logger()
    logger.info(f'Spinning up Test vQPU-{vqpu_id} backend')
    await create_vqpu_remote_yaml(job_info, vqpu_id=vqpu_id)
    cmds = ['echo', 'justtesting']
    process = run_a_process(cmds)
    await asyncio.sleep(5)
    event.set()
    logger.info(f'Test vQPU-{vqpu_id} running ... ')

async def task_circuitcomplete(
        event : EventFile, 
        vqpu_id : int
        ) -> str:
    """Async task that indicates why vqpu can proceed to be shutdown as all circuits have been run

    Args:
        event (EventFile): The event file that is used to mark circuits have completed 
        vqpu_id (int): The vqpu id

    Returns: 
        returns a message of what has completed 
    """
    await event.wait()
    return "Circuits completed!"

async def task_testcircuitcomplete(
        event : EventFile, 
        circuittime : float, 
        vqpu_id : int
        ) -> str:
    """Async task that indicates why vqpu can proceed to be shutdown as all the circuits have been run mimicked by a time to wait

    Args:
        event (EventFile): The event file that is used to mark circuits have completed 
        vqpu_id (int): The vqpu id
    Returns: 
        returns a message of what has completed 
    """
    await asyncio.sleep(circuittime)
    event.set()
    return "Circuits completed!"

async def task_walltime(walltime : float, vqpu_id : int) -> str:
    """Async task that indicates why vqpu can proceed to be shutdown as has exceeded walltime

    Args:
        walltime (float): Walltime to wait before shutting down vqpu 
        vqpu_id (int): The vqpu id
    Returns: 
        returns a message of what has completed 
    """
    await asyncio.sleep(walltime)
    return 'Walltime complete'

@task(retries = 0, 
      task_run_name = 'Task-Run-vQPU-{vqpu_id}',
      )
async def run_vqpu(
    event : EventFile,
    vqpu_id : int,
    walltime : float = 86400, # one day
    ) -> None:
    """Runs a task that waits to keep flow active
    so long as there are circuits to be run or have not exceeded the walltime

    Args:
        event (EventFile): The event file that is used to mark circuits have completed 
        vqpu_id (int): The vqpu id
        walltime (float): Walltime to wait before shutting down vqpu 
    """
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
    """Runs a task that waits to keep vqpu flow active. Only a test for checking prefect works 

    Args:
        event (EventFile): The event file that is used to mark circuits have completed 
        vqpu_id (int): The vqpu id
        walltime (float): Walltime to wait before shutting down vqpu 
        circuittime (float): mimic circuits completing
    """
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
      timeout_seconds=600,
      task_run_name = 'Task-Shutdown-vQPU-{vqpu_id}',
      )
async def shutdown_vqpu(
    arguments: str, 
    vqpu_id : int,
    ) -> None:
    """Shuts down vqpu 

    Args:
        arguments (str): Argument that contains what process to kill   
        vqpu_id (int): The vqpu id
    """
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

def getcircuitandpost(c : Any) -> Tuple:
    """process a possible circuit and any post processing to do 

    Args:
        c : possible combintation of circuit and post or just circuit
    """

    if not (isinstance(c, Callable) or isinstance(c, Tuple)):
        errmessage : str = "circuits argument was passed a List not containing either a callable function"
        errmessage += " or a tuple of callable fuctions consisting of a circuit and some post processing"
        errmessage += f"got type {type(c)}"
        raise ValueError(errmessage)
    # if the list contains a tuple then the circuit is 
    # followed by some post processing
    post = None
    circ = c
    if isinstance(c, Tuple) :
        # unpack the tuple and store the postprocessing call, p
        circ, post = c
        if not isinstance(circ, Callable) and not isinstance(post, Callable):
            errmessage : str = "circuits argument was passed a Tuple that did not consist of two callable functions"
            errmessage += " a circuit and some post processing"
            errmessage += f"got type {type(circ)} and {type(post)}"
            raise ValueError(errmessage)
    return circ, post


@task(retries = 10, 
      retry_delay_seconds = 2,
      retry_jitter_factor = 0.5,
      task_run_name = 'Run_circuit-{circuitfunc.__name__}-vqpu-{vqpu_id}',
      )
async def run_circuit(
    circuitfunc : Callable, 
    vqpu_id : int = 1,
    arguments : str = '',
    remote : str = '', 
    ) -> Tuple[Dict[str, int], int]:
    """Run a circuit on a given remote backend

    Args:
        circuitfunc (Callable) : function to run 
        vqpu_id (int): The vqpu id to use 
        arguments (str): string of arguments to pass to circuit function
        remote (str) : remote vQPU to use 

    Returns:
        Tuple[Dict[str, int], int]: Dictionary results from a circuit that consists of bitstrings and counts along with the id of this task running the circuit
    """
    # note that below since artifcate saves remote file name, don't need the below check and parsing
    results = circuitfunc(remote, arguments)
    context = get_run_context()
    task_run_id = context.task_run.id
    return results, task_run_id

@task(retries = 10, 
      retry_delay_seconds = 2,
      retry_jitter_factor=0.5,
      task_run_name = 'Run_postprocess-{postprocessfunc.__name__}-circuit_id-{circuit_job_id}',
      )
async def run_postprocess(
    postprocessfunc : Callable, 
    initial_results : Dict[str,int],
    circuit_job_id : int, 
    arguments : str, 
    ) -> Tuple[Any, int]:
    """Run a circuit on a given remote backend

    Args:
        postprocessfunc (Callable) : function to run postprocessing results from a circuit
        initial_results (Dict[str,int]) : results from the circuit 
        circuit_job_id (int): The job id of the circuit producing the initial results 
        arguments (str): string of arguments to pass to circuit function

    Returns:
        Tuple[Any, int]: The postprocessing rsult and task running the postprocessing
    """
    # note that below since artifcate saves remote file name, don't need the below check and parsing
    results = await postprocessfunc(initial_results, arguments)
    context = get_run_context()
    task_run_id = context.task_run.id
    return results, task_run_id

@task(retries = 10, 
      retry_delay_seconds = 2,
      retry_jitter_factor = 0.5,
      task_run_name = 'Run_circuitsandpost-vqpu-{vqpu_id}',
      )
async def run_circuitandpost(
    circuit : Callable | Tuple[Callable, Callable],
    vqpu_id : int,
    arguments : str,
    remote : str,
    produce_histo : bool = True,
    ) -> Dict[str, Any]:
    """Run circuit (and possibly postprocessing) on a given remote backend

    Args:
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
    circ, post = getcircuitandpost(circuit)
    logger.info(f'Running {circ.__name__}')
    future = await run_circuit.submit(
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
        postprocess_args = f' --filename=plots/{circ.__name__}.vqpu-{vqpu_id}.{circ_id}.histo --plottitle=testing '
        future = await run_postprocess.submit(
            postprocessfunc = post, 
            initial_results = circ_results,
            circuit_job_id = circ_id, 
            arguments = postprocess_args, 
        )
        post_results, post_id = await future.result()
        results['post'] = {'results': post_results, 'name': post.__name__, 'id': post_id}
        logger.debug(f'Results from postprocessing {post.__name__} : {post_results}')
    return results


@flow(name = "Launch vQPU Flow", 
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
    """Flow for running vqpu 

    Args:
        launch_event (EventFile) : event file that is marked once the vqpu has launched
        finish_circuit_event (EventFile) : event file that checked to see all the circuits have been run
        vqpu_id (int): vqpu id 
        walltime (float) : walltime to keep running the vqpu 
        arguments (str): string of arguments to pass extra options to launching of vqpu

    """
    # clean up before launch
    launch_event.clean()
    delete_vqpu_remote_yaml(vqpu_id)
    logger = get_run_logger()

    # now launch
    logger.info(f'Launching vQPU-{vqpu_id}')
    future = await launch_vqpu.submit(launch_event, vqpu_id = vqpu_id, arguments = arguments)
    await future.result()
    
    # now run it 
    logger.info(f'vQPU-{vqpu_id} running and will keep running till circuits complete or hit walltime ... ')
    future = await run_vqpu.submit(finish_circuit_event, vqpu_id = vqpu_id, walltime = walltime)
    await future.result()

    # once the run has finished, shut it down
    future = await shutdown_vqpu.submit(arguments = arguments, vqpu_id = vqpu_id)

@flow(name = "Launch Test vQPU Flow", 
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
    """Flow for testing the launch and running of vqpu. For testing Prefect orchestration

    Args:
        launch_event (EventFile) : event file that is marked once the vqpu has launched
        finish_circuit_event (EventFile) : event file that checked to see all the circuits have been run
        vqpu_id (int): vqpu id 
        walltime (float) : walltime to keep running the vqpu 
        circuittime (float) : mimic circuits finishing to keep running the vqpu
        arguments (str): string of arguments to pass extra options to launching of vqpu

    """
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
    circuits : List[Callable | Tuple[Callable, Callable]],
    vqpu_id : int = 1,  
    arguments : str = "", 
    delay_before_start : float = 10.0, 
    circuits_complete : bool = True, 
    date : datetime.datetime = datetime.datetime.now() 
    ) -> None:
    """Flow for running circuits (and any postprocessing)

    Args:
        vqpu_event (EventFile) : event file that is checked to see if vqpu is launched
        circuit_event (EventFile) : event file that is marked once all the circuits have been run
        circuits (List[Callable | Tuple[Callable, Callable]]): circuits (and postprocessing) to run
        vqpu_id (int): vqpu id 
        arguments (str): string of arguments to pass extra options to launching of vqpu

    """
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
    results = list()
    for c in circuits:
        result = await run_circuitandpost.fn(circuit=c, vqpu_id=vqpu_id, arguments=arguments, remote=remote)
        results.append(result)
    logger.info('Finished all running all circuits')
    if circuits_complete: circuit_event.set()
    return results

@task 
def circuit_event_clean(
    events : Dict[str,EventFile],
    vqpu_ids : List[int]
    ) -> None:
    """Flow for running circuits (and any postprocessing)

    Args:
        events (Dict[str,EventFile]) : event files 
        vqpu_ids (List[int]): list of vqpu ids
    """
    for vqpu_id in vqpu_ids:
        key = f'vqpu_{vqpu_id}_circuits_finished'
        events[key].clean()

@task
async def run_circuits_once_vqpu_ready(
    task_runners : Dict[str, DaskTaskRunner | Dict[str,str]],
    events : Dict[str, EventFile], 
    circuits : Dict[str, List[Callable | Tuple[Callable, Callable]]],
    vqpu_id : int,  
    arguments : str, 
    circuits_complete : bool = True, 
    run_silly_compute_test : bool = True,
    ):
    results = list()
    logger = get_run_logger()
    key = f'vqpu_{vqpu_id}'
    await events[key+'_launch'].wait()
    artifact = await Artifact.get(key=f'remote{vqpu_id}')
    remote = dict(artifact)['data'].split('\n')[1]
    logger.info(f'vqpu-{vqpu_id} running, submitting circuits ...')
    for c in circuits[key]:
        result = await run_circuitandpost.fn(circuit=c, vqpu_id=vqpu_id, arguments=arguments, remote=remote)
        results.append(result)
        # and for now randomly launch a gpu and cpu flow based on 
        # some random number 
        if run_silly_compute_test:
            if (np.random.uniform() > 0.5):
                await gpu_workflow.with_options(
                    task_runner = task_runners['gpu']
                )(arguments = arguments)
            for i in range(4):
                if (np.random.uniform() > 0.2):
                    await cpu_workflow.with_options(
                        task_runner = task_runners['cpu']
                    )(arguments = arguments)
    if circuits_complete: events[key+'_circuits_finished'].set()
    return results

@flow(name = "Circuits with multi-vQPU, GPU flow", 
      flow_run_name = "circuits_flow_for_vqpu_{vqpu_ids}_on-{date:%Y-%m-%d:%H:%M:%S}",
      description = "Running circutis on the vQPU with the appropriate task runner for launching circuits and also launches a gpu workflow, ", 
      retries = 3, 
      retry_delay_seconds = 20, 
      log_prints = True,
      )
async def circuits_with_nqvpuqs_workflow(
    task_runners : Dict[str, DaskTaskRunner | Dict[str,str]],
    events : Dict[str, EventFile], 
    circuits : Dict[str, List[Callable | Tuple[Callable, Callable]]],
    vqpu_ids : List[int],  
    arguments : str, 
    delay_before_start : float = 10.0,
    circuits_complete : bool = True, 
    run_silly_compute_test : bool = True,
    date : datetime.datetime = datetime.datetime.now() 
    ) -> None:
    """Flow for running circuits (and any postprocessing) on multiple vqpus

    Args:
        task_runners (Dict[str, DaskTaskRunner | Dict[str,str]]): The list of DaskTaskRunner to use when running flows (along with string of the jobscript)
        events (Dict[str,EventFile]) : event files 
        vqpu_ids (List[int]): list of vqpu ids
        circuits (Dict[str, List[Callable | Tuple[Callable, Callable]]]): circuits (and postprocessing) to run for a given vqpu 
        arguments (str): string of arguments to pass extra options to running circuits and postprocessing

    """

    logger = get_run_logger()

    if (delay_before_start < 0): delay_before_start = 0
    # clean-up any events 
    for vqpu_id in vqpu_ids:
        key = f'vqpu_{vqpu_id}_circuits_finished'
        events[key].clean()

    # run delay 
    if delay_before_start > 0:
        logger.info(f'Delay of {delay_before_start} seconds before starting ... ')
        await asyncio.sleep(delay_before_start)

    logger.info(f'Waiting for vqpu-{vqpu_ids} to start ... ')

    # due to task runners, serializing the run_circuits_once_vqpu_ready
    # task cannot be submitted using future = await run_circuits_once_vqpu_ready.submit() it appears
    # but an easy option is to use a task group 
    tasks = dict()
    async with asyncio.TaskGroup() as tg:
        # either spin up real vqpu
        for vqpu_id in vqpu_ids:
            tasks[vqpu_id] = tg.create_task(run_circuits_once_vqpu_ready(
                task_runners = task_runners,
                events = events,
                circuits = circuits,
                vqpu_id = vqpu_id, 
                arguments = arguments, 
                circuits_complete=circuits_complete,
                run_silly_compute_test=run_silly_compute_test,
                )
            )
    results = {f'vqpu-{name}': task.result() for name, task in tasks.items()}
    logger.debug(results)
    return results

@task(retries = 10, 
      retry_delay_seconds = 2,
      timeout_seconds=3600,
      )
async def run_cpu(arguments: str) -> None:
    """Running cpu based programs. Here is somewhat hardcoded for testing 

    Args:
        arguments (str): string of arguments to pass extra options to run cpu 
    """
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
      )
async def run_gpu(arguments: str) -> None:
    """Running gpu based programs. Here is somewhat hardcoded for testing 

    Args:
        arguments (str): string of arguments to pass extra options to run cpu 
    """
    logger = get_run_logger()
    logger.info("Launching GPU task")
    cmds = arguments.split('--gpu-exec=')[1].split(' ')[0].split(',')
    cmds += arguments.split('--gpu-args=')[1].split(' ')[0].split(',')
    logger.info(cmds)
    process = run_a_process(cmds)
    logger.info("Finished GPU task")

@flow(name = "Simple CPU flow", 
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
    """Flow for running cpu based programs.

        arguments (str): string of arguments to pass extra options to run cpu 
    """
    logger = get_run_logger()
    logger.info("Launching CPU flow")
    # submit the task and wait for results 
    future = await run_cpu.submit(arguments)
    await future.result()
    logger.info("Finished CPU flow")

@flow(name = "Simple GPU flow", 
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
    """Flow for running gpu based programs.
    
        arguments (str): string of arguments to pass extra options to run cpu 
    """
    logger = get_run_logger()
    logger.info("Launching GPU flow")
    # submit the task and wait for results 
    future = await run_gpu.submit(arguments)
    await future.result()
    logger.info('Finished GPU flow')
    