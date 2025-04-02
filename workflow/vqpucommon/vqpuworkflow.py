"""
@file vqpuworkflow.py
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
import asyncio
from prefect_dask import DaskTaskRunner
from prefect import flow, task, get_client, pause_flow_run
from prefect.logging import get_run_logger
from prefect.artifacts import Artifact
from prefect.context import get_run_context, TaskRunContext
from prefect.serializers import Serializer, JSONSerializer 

class HybridQuantumWorkflowBase:
    """
    A class that contains basic tasks for running hybrid vQPU workflows.
    """

    def __init__(self, 
                 cluster : str, 
                 vqpu_ids : List[int] = [i for i in range(100)], 
                 name : str = 'myflow', 
                 backends : List[str] | None = None, 
                 eventloc : str | None = None, 
                 vqpu_script : str | None = None, 
                 vqpu_template_yaml : str | None = None, 
                 vqpu_yaml_dir : str | None = None, 
                 vqpu_exec : str | None = None, 
                 events : Dict[str, EventFile] | None = None,
                 ):
        """
        Constructs all the necessary attributes for the person object.

        Args:
            cluster (str): cluster name.
            maxvqpu (int): max number of vqpus to launch
        """

        self.name: str
        """name of workflow"""
        self.cluster: str
        """cluster name for slurm configurations"""
        self.maxvqpu: int = 100
        """max number of virtual qpus"""
        self.vqpu_script : str =  f'{os.path.dirname(os.path.abspath(__file__))}/../qb-vqpu/vqpu.sh'
        """vqpu start up script to run"""
        self.vqpu_temmplate_yaml : str = f'{os.path.dirname(os.path.abspath(__file__))}/remote_vqpu_template.yaml'
        """vqpu remote yaml template"""
        self.vqpu_yaml_dir : str = f'{os.path.dirname(os.path.abspath(__file__))}/../vqpus/'
        """directory where to store the active vqpu yamls"""
        self.vqpu_exec : str = 'qcstack'
        """vqpu executable. Default is QB's vQPU executable"""
        self.vqpu_ids : List[int]
        """list of active vqpus"""
        self.events : Dict[str, EventFile] = dict()
        """list of events"""
        self.eventloc : str = f'{os.path.dirname(os.path.abspath(__file__))}/../events/'
        """location of where to store event files"""
        self.taskrunners : Dict[str, DaskTaskRunner | Dict[str,str]]
        """The slurm configuration stored in the DaskTaskRunner used by Prefect"""
        self.backends : List[str] = ['qb-vqpu', 'braket', 'quera', 'qiskit', 'pennylane', 'cudaq']
        """List of allowed backends"""

        self.name = name
        self.cluster = cluster
        self.taskrunners = get_dask_runners(cluster=self.cluster)
        # check that taskrunners has minimum set of defined cluster configurations
        clusconfigs = list(self.taskrunners.keys())
        reqclusconfigs = ['generic', 'circuit', 'vqpu', 'cpu', 'gpu']
        valerr = []
        for elem in reqclusconfigs:
            if elem not in clusconfigs:
                valerr.append(elem)
        if len(valerr) > 0:
            raise ValueError(f'Missing cluster configurations. Minimum set required is {reqclusconfigs}. Missing {valerr}')
            
        if backends != None:
            self.backends = backends
        # Do I check backends? 
        reqbackends = ['qiskit', 'pennylane']
        valerr = []
        for elem in reqbackends:
            if elem not in self.backends:
                valerr.append(elem)
        if len(valerr) > 0:
            raise ValueError(f'Missing minimum req backends. Minimum set required is {reqbackends}. Missing {valerr}')
        
        self.vqpu_ids = vqpu_ids
        if eventloc != None:
            self.eventloc = eventloc
        if vqpu_yaml_dir != None:
            self.vqpu_yaml_dir = vqpu_yaml_dir
        if vqpu_exec != None:
            self.vqpu_exec = vqpu_exec
        if vqpu_script != None:
            self.vqpu_script = vqpu_script
        if vqpu_template_yaml != None:
            self.vqpu_template_yaml = vqpu_template_yaml
        if events == None:
            if 'qb-vqpu' in self.backends:
                for vqpu_id in self.vqpu_ids:
                    self.events[f'vqpu_{vqpu_id}_launch'] = EventFile(name = f'vqpu_{vqpu_id}_launch', loc = self.eventloc) 
                    self.events[f'vqpu_{vqpu_id}_circuits_finished'] = EventFile(name = f'vqpu_{vqpu_id}_circuits_finished', loc = self.eventloc)
        else:
            # do a deep copy 
            self.events = copy.deepcopy(events)

    def __str__(self):
        message : str = f'Hybrid Quantum Workflow {self.name} running on\n'
        message += f'Cluster : {self.cluster}\n'
        for key, value in self.taskrunners['jobscript'].items():
            message += f'Slurm configuration {key}: {value}\n'
        message += f'Allowed QPU backends : {self.backends}\n'
        return message

    def report_config(self):
        """
        Report config of the hybrid workflow
        """
        logger = get_run_logger()
        message = self.__str__()
        logger.info(message)

    def to_dict(self) -> Dict:
        """Converts class to dictionary for serialisation
        """
        eventdict = dict()
        for k,e in self.events.items():
            eventdict[k]=e.to_dict()
        return {
            'HybridQuantumWorkflowBase' : {
            'name' : self.name, 
            'cluster': self.cluster,
            'maxvqpu': self.maxvqpu,
            'vqpu_script':self.vqpu_script,
            'vqpu_template_yaml': self.vqpu_temmplate_yaml,
            'vqpu_yaml_dir': self.vqpu_yaml_dir,
            'vqpu_exec': self.vqpu_exec,
            'vqpu_ids': self.vqpu_ids,
            'events': eventdict,
            'eventloc': self.eventloc,
            'backends' : self.backends,
            }
        }

    @classmethod
    def from_dict(cls, data : Dict):
        """Create an object from a dictionary
        """
        # print('hybridworlfow from_dict', data.keys())
        if 'HybridQuantumWorkflowBase' not in list(data.keys()):
            raise ValueError('Not an HybridQuantumWorkflowBase dictionary')
        data = data['HybridQuantumWorkflowBase']
        eventdict = dict()
        for k,e in data['events'].items():
            eventdict[k]=EventFile.from_dict(e)
        return cls(
            name=data['name'],
            cluster=data['cluster'],
            # maxvqpu=data['maxvpuq'],
            vqpu_script=data['vqpu_script'],
            vqpu_template_yaml=data['vqpu_template_yaml'],
            vqpu_yaml_dir=data['vqpu_yaml_dir'],
            vqpu_exec=data['vqpu_exec'],
            vqpu_ids=data['vqpu_ids'],
            events=eventdict,
            backends=data['backends'],
            )

    def __eq__(self, other):
        if isinstance(other, HybridQuantumWorkflowBase):
            return self.to_dict() == other.to_dict()
        return False
        
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

    async def launch_vqpu(
            self,
            job_info : SlurmInfo,  
            vqpu_id : int,
            spinuptime : float,
            ) -> None:
        """Launchs the vqpu service and generates events to indicate it has been launched

        Args:
            job_info (SlurmInfo) : gets the slurm job info related to spinning up the service
            vqpu_id (int) : The vqpu id
            spinuptime (float) : The time to wait before setting the event 
        """
        await self.__create_vqpu_remote_yaml(job_info, vqpu_id=vqpu_id)
        cmds = [self.vqpu_script]
        process = run_a_process(cmds)
        await asyncio.sleep(spinuptime)
        self.events[f'vqpu_{vqpu_id}_launch'].set()

    async def shutdown_vqpu(
            self,
            vqpu_id : int):
        """Shuts down the vqpu service 

        Args:
            vqpu_id (int) : The vqpu id
        """
        self.__delete_vqpu_remote_yaml(vqpu_id)
        for proc in psutil.process_iter():
            # check whether the process name matches
            if proc.name() == self.vqpu_exec:
                proc.kill()

    def cleanupbeforestart(
            self,
            vqpu_id : int | None = None):
        if vqpu_id == None:
            for vqpu_id in self.vqpu_ids:
                self.events[f'vqpu_{vqpu_id}_launch'].clean()
                self.__delete_vqpu_remote_yaml(vqpu_id)
        else:
            self.events[f'vqpu_{vqpu_id}_launch'].clean()
            self.__delete_vqpu_remote_yaml(vqpu_id)

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

    def getcircuitandpost(
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

    async def getremoteaftervqpulaunch(
            self,
            vqpu_id : int,
    ) -> str:
        """Waits for a vqpu launch event and then returns the path to the yaml file that defines access to the remote service 

        Args:
            vqpu_id (int): vqpu_id to wait for and get remote of
        """
        await self.events[f'vqpu_{vqpu_id}_launch'].wait()
        artifact = await Artifact.get(key=f'remote{vqpu_id}')
        remote = dict(artifact)['data'].split('\n')[1]
        return remote

    def checkbackends(
            self, 
            backend : str = 'qb-vqpu', 
            checktype : str = 'launch'
            ) -> None:
        """Runs a number of checks of the backend to see if enabled set of backends

        Args:
            backend (str): backend to check
            checktype (str): whether checking launch or circuits or other types
        """
        if backend == '':
            return 
        if checktype == 'launch':
            if backend == 'qb-vqpu' and 'qb-vqpu' not in self.backends: 
                raise RuntimeError(f'vQPU requested to be launched yet qb-vqpu not in list of acceptable backends. Allowed backends {self.backends}. Terminating')
            elif backend not in self.backends:
                raise RuntimeError(f'{backend} requested to be used but not in list of acceptable backends. Allowed backends {self.backends}. Terminating')
        if checktype == 'circuit':
            if backend not in self.backends: 
                raise RuntimeError(f"Circuits running using unknown backend {backend}. Allowed backends: {self.backends}. Terminating")


    def checkvqpuid(self, vqpu_id : int) -> None:
        """Checks if vqpu_id in allowed ids

        Args:
            vqpu_id (int): id to check
        """
        if vqpu_id not in self.vqpu_ids:
            raise RuntimeError(f'vQPU ID {vqpu_id} requested yet not in allowed list of ids. Allowed ids {self.vqpu_ids}. Terminating')
        pass 

# class HybridWorkflowSerializer(Serializer):
#     def dumps(self, obj) -> bytes:
#         # Serialize the object to JSON-compatible bytes
#         return json.dumps(obj.to_dict()).encode('utf-8')

#     def loads(self, blob: bytes):
#         # Deserialize the JSON bytes back into an object
#         data = json.loads(blob.decode('utf-8'))
#         return HybridQuantumWorkflowBase.from_dict(data)

# Custom encoder and decoder functions
### Key Points:
# - Type Checking: Use Python's isinstance() to determine the class of the object during serialization.
# - Type Field: Include a type field in your serialized data to help identify which class the data should be deserialized into.
# - Error Handling: Implement error handling to manage unknown types gracefully.
# @classmethod
# def custom_object_encoder(obj):
#     if isinstance(obj, HybridQuantumWorkflowBase):
#         return obj.to_dict()
#     raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

# @classmethod
# def custom_object_decoder(dct):
#     if 'custom_class_marker' in dct:
#         return HybridQuantumWorkflowBase.from_dict(dct)
#     return dct


# I think the best bet is to define a custom serializer 

class HybridQuantumWorkflowSerializer(JSONSerializer):

    # import types
    standard_types = [
    int, float, str, bool, list, tuple, dict, set, complex, 
    ]
    """list standard types which the JSONSerializer should be able to handle"""

    def serialize(self, value : Any) -> Dict:
        """Return a dictionary. Assumes that classes have a to_dict() function
        
        Args: 
            value (Any) : value to serialize to a dictionary 
        
        Returns:
            returns a dictionary that JSON serialize would be happy with 
        """
        print(type(value))
        if isinstance(value, HybridQuantumWorkflowBase):
            return value.to_dict()
        elif isinstance(value, EventFile):
            return value.to_dict()
        # elif type(value) not in self.standard_types:
        #     return value.to_dict()
        return super().dumps(value)

    def deserialize(self, value : Dict) -> Any:
        """Return an instance of a class or some value
        
        Args: 
            value (Dict) : value to deserialize to a an instance of some class 
        
        Returns:
            returns an instance of some class or simple object 
        """
        try:
            return HybridQuantumWorkflowBase.from_dict(value)
        except Exception:
            try:
                return EventFile.from_dict(value)
            except:
                return super().loads(value)

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
        asyncio.create_task(myqpuworkflow.task_circuitcomplete(myqpuworkflow.events[f'vqpu_{vqpu_id}_circuits_finished'], vqpu_id=vqpu_id)),
        asyncio.create_task(myqpuworkflow.task_walltime(walltime, vqpu_id=vqpu_id)),
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
    future = await shutdown_vqpu.submit(myqpuworkflow = myqpuworkflow, arguments = arguments, vqpu_id = vqpu_id)
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
        task_runner = myqpuworkflow.taskrunners['circuit'],
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
        task_runner = myqpuworkflow.taskrunners['cpu'],
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
        task_runner = myqpuworkflow.taskrunners['gpu'],
    )
    asyncio.run(my_flow(myqpuworkflow=myqpuworkflow, execs=execs, arguments=arguments))


# class DaskTaskRunners:
#     def __init__(self, cluster : str):
#         self.taskrunners : Dict[str, DaskTaskRunner | Dict[str,str]] = get_dask_runners(cluster)
    
#     def to_dict(self) -> Dict:
#         """Converts class to dictionary for serialisation
#         """
#         tr = dict()
#         for k in list(self.taskrunners.keys()):
#             if k == 'jobscript':
#                 tr[k] = copy.deepcopy(self.taskrunners[k])

#         return {
#             'DaskTaskRunners' : {
#             'cluster': self.cluster,

#             }
#         }

class SillyTestClass:
    """To run unit tests and check flows
    """
    y : int = 0
    def __init__(self, x=2):
        self.x : float = x
    
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
