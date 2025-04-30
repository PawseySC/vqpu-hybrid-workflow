"""
@file vqpubase.py
@brief Collection of tasks and flows related to vqpu and circuits

"""

import time, datetime, subprocess, os, select, psutil, copy
import functools
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

    reqclusconfigs : List[str] = ['generic', 'circuit', 'vqpu', 'cpu', 'gpu']
    """List of required configurations for running workflow."""
    vqpu_allowed_backends : Dict[str, str] = {
        'qsim': 'Description: A noise-aware, GPU-enabled state-vector simulator developed by Quantum Brilliance, built atop the Google Cirq qsim simulator',
        'sparse-sim': 'Description: Microsoft Quantum sparse state-vector simulator allows a high number of qubits to be simulated whenever the quantum circuit being executed preserves sparsity. It utilises a sparse representation of the state vector and methods to delay the application of some gates (e.g. Hadamard).',
        'qpp': 'Description: Quantum++ state vector simulator, configured to use XACC IR',
        'cuda:qpp': 'Description: Quantum++ state vector simulator using QIR',
        'custatevec:fp32': 'Description: Single (custatevec:fp32) CUDA Quantum state vector simulator, built on CuQuantum libraries.',
        'custatevec:fp64': 'Description: double-precision CUDA Quantum state vector',
        'aer': 'Description: IBM Qiskit Aer noise-aware state-vector (and MPS and density-matrix simulator',
        'qb-mps': 'Description: Quantum Brilliance noise-aware MPS simulator, configured to use XACC IR (qb-mps). The MPS method represents the quantum wavefunction as a tensor contraction of individual qubit quantum state. Each qubit quantum state is a rank-3 tensor (rank-2 tensor for boundary qubits).',
        'cudaq:qb_mps': 'Description: MPS using QIR.',
        'qb-mpdo': 'Description: Quantum Brilliance noise-aware matrix-product density operator (MPDO) simulator, configured to use XACC IR (qb-mpdo). The MPDO method represents the density matrix as a tensor contraction of individual qubit density operator. Each qubit density operator is a rank-4 tensor (rank-3 tensor for boundary qubits).',
        'cudaq:qb_mpdo': 'Description: MPDO using QIR',
        'qb-purification': 'Descrption: Quantum Brilliance noise-aware state purification simulator, configured to use XACC IR (qb-purification). The purification method represents the purified quantum state as a tensor contraction of individual qubit purified state. Each qubit purified state is a rank-4 tensor (rank-3 tensor for boundary qubits).',
        'cudaq:qb_purification': 'Description: noise-aware state purification using QIR',
        'cudaq:dm': 'Description: The CUDA Quantum density matrix simulator, built on CuQuantum libraries',
    }

    vqpu_backend_default : str = 'qpp'
 
    def __init__(self, 
                 cluster : str, 
                 vqpu_ids : List[int] = [i for i in range(100)], 
                 name : str = 'myflow', 
                 vqpu_backends : List[str] | None = None, 
                 backends : List[str] | None = None, 
                 eventloc : str | None = None, 
                 vqpu_template_script : str | None = None, 
                 vqpu_template_yaml : str | None = None, 
                 vqpu_run_dir : str | None = None, 
                 vqpu_exec : str | None = None, 
                 events : Dict[str, EventFile] | None = None,
                 ):
        """
        Constructs all the necessary attributes for the person object.

        Args:
            cluster (str): cluster name.
            maxvqpu (int): max number of vqpus to launch
        """
        fpath : str = str(os.path.dirname(os.path.abspath(__file__)))
        self.name: str
        """name of workflow"""
        self.cluster: str
        """cluster name for slurm configurations"""
        self.maxvqpu: int = 100
        """max number of virtual qpus"""
        self.vqpu_template_script : str = f'{fpath}/../qb-vqpu/vqpu_template.sh'
        """template vqpu start up script to run"""
        self.vqpu_template_yaml : str = f'{fpath}/../qb-vqpu/remote_vqpu_template.yaml'
        """vqpu remote yaml template"""
        self.vqpu_run_dir : str = f'{os.path.dirname(os.path.abspath(__file__))}/../vqpus/'
        """directory where to store the active vqpu yamls and scripts"""
        self.vqpu_exec : str = 'qcstack'
        """vqpu executable. Default is QB's vQPU executable"""
        self.vqpu_ids : List[int]
        """list of active vqpus"""
        self.vqpu_backends : List[str]
        """list of backend end used of the vqpu (state-vector, density-matrix, MPS)"""
        self.events : Dict[str, EventFile] = dict()
        """list of events"""
        self.eventloc : str = f'{os.path.dirname(os.path.abspath(__file__))}/../events/'
        """location of where to store event files"""
        # before taskrunners also stored the DaskTaskRunner but this leads to issues 
        # with serialization. Now just store the slurm job script
        # self.taskrunners : Dict[str, DaskTaskRunner | Dict[str,str]]
        self.taskrunners : Dict[str, str]
        """The slurm configuration stored in the DaskTaskRunner used by Prefect"""
        self.backends : List[str] = ['qb-vqpu', 'braket', 'quera', 'qiskit', 'pennylane', 'cudaq']
        """List of allowed backends"""

        self.name = name
        self.cluster = cluster
        # note before had the taskrunners store the actual DaskTaskRunner instances but this generated problems with serialization. 
        # So instead will store the configuration but not the actual task runner. 
        # Now there is a function that will get the appropriate task runner 
        self.taskrunners = copy.deepcopy(get_dask_runners(cluster=self.cluster)['jobscript'])
        # check that taskrunners has minimum set of defined cluster configurations
        clusconfigs = list(self.taskrunners.keys())
        valerr = []
        for elem in self.reqclusconfigs:
            if elem not in clusconfigs:
                valerr.append(elem)
        if len(valerr) > 0:
            raise ValueError(f'Missing cluster configurations. Minimum set required is {self.reqclusconfigs}. Missing {valerr}')
            
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
        # set the default backend. Not clear if this structure is really necessary
        if vqpu_backends != None:
            self.vqpu_backends = vqpu_backends
            if (len(self.vqpu_backends)<len(self.vqpu_ids)):
                self.vqpu_backends.append(None)
        else:
            self.vqpu_backends = None

        if eventloc != None:
            self.eventloc = eventloc
        if vqpu_run_dir != None:
            self.vqpu_run_dir = vqpu_run_dir
        if vqpu_exec != None:
            self.vqpu_exec = vqpu_exec
        if vqpu_template_script != None:
            self.vqpu_template_script = vqpu_template_script
        if vqpu_template_yaml != None:
            self.vqpu_template_yaml = vqpu_template_yaml
        # this is very odd, don't know why default is being saved as tuple
        if isinstance(self.vqpu_template_yaml, tuple):
            self.vqpu_template_yaml = self.vqpu_template_yaml[0]
            
        if not os.path.isfile(self.vqpu_template_yaml):
            message : str = f'Template yaml file {self.vqpu_template_yaml} not found. Please ensure file exists.'
            raise ValueError(message)        
        if not os.path.isfile(self.vqpu_template_script):
            message : str = f'Template vqpu start-up script file {self.vqpu_template_script} not found. Please ensure file exists.'
            raise ValueError(message)

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
        for key, value in self.taskrunners.items():
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
            'vqpu_script':self.vqpu_template_script,
            'vqpu_template_yaml': self.vqpu_template_yaml,
            'vqpu_run_dir': self.vqpu_run_dir,
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
            vqpu_template_script=data['vqpu_script'],
            vqpu_template_yaml=data['vqpu_template_yaml'],
            vqpu_run_dir=data['vqpu_run_dir'],
            vqpu_exec=data['vqpu_exec'],
            vqpu_ids=data['vqpu_ids'],
            events=eventdict,
            backends=data['backends'],
            )

    def __eq__(self, other):
        if isinstance(other, HybridQuantumWorkflowBase):
            return self.to_dict() == other.to_dict()
        return False
        
    def gettaskrunner(self, task_runner_name : str) -> DaskTaskRunner:
        """Returns the appropriate task runner. 
        Args:
            task_runner_name (str): name of dasktaskrunner configuration 
        Returns:
            A DaskTaskRunner
        """
        runners = get_dask_runners(cluster=self.cluster)
        if task_runner_name not in list(self.taskrunners.keys()):
            raise ValueError(f'Cluster {self.cluster} configuration does not have runner {task_runner_name}.')
        return runners[task_runner_name]

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
        workflow_yaml = f'{self.vqpu_run_dir}/remote_vqpu-{vqpu_id}.yaml'
        # here there is an assumption of the path to the template
        lines = open(self.vqpu_template_yaml, 'r').readlines()
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

        workflow_yaml = f'{self.vqpu_run_dir}/remote_vqpu-{vqpu_id}.yaml'
        if os.path.isfile(workflow_yaml):
            os.remove(workflow_yaml)

    async def __create_vqpu_script(
        self, 
        vqpu_id : int,
        vqpu_backend : str | None = None, 
        ) -> str:
        """Saves the script backend for the vqpu to a yaml file and artifact
        having extracted the hostname running the vqpu from the slurm job

        Args:
            vqpu_id (int): The vqpu id
            vqpu_backend (str): The backend to use for this vqpu
        """
        # where to save the vqpu start up script
        vqpu_script = f'{self.vqpu_run_dir}/vqpu-{vqpu_id}.sh'
        # here there is an assumption of the path to the template
        lines = open(self.vqpu_template_script, 'r').readlines()
        fout = open(vqpu_script, 'w')
        if vqpu_backend == None: vqpu_backend = self.vqpu_backend_default
        self.checkvqpubackends(vqpu_id = vqpu_id, vqpu_backend=vqpu_backend)
        
        # update the backend 
        for line in lines:
            if 'MY_VQPU_BACKEND' in line:
                line = line.replace('MY_VQPU_BACKEND', vqpu_backend)
            fout.write(line)
        # to store the results of this task, make use of a helper function that creates artifcat 
        await save_artifact(vqpu_script, key=f'vqpuscript{vqpu_id}')
        return vqpu_script

    async def launch_vqpu(
            self,
            job_info : SlurmInfo,  
            vqpu_id : int,
            spinuptime : float,
            vqpu_backend : str | None = None, 
            ) -> None:
        """Launchs the vqpu service and generates events to indicate it has been launched

        Args:
            job_info (SlurmInfo) : gets the slurm job info related to spinning up the service
            vqpu_id (int) : The vqpu id
            spinuptime (float) : The time to wait before setting the event 
        """
        await self.__create_vqpu_remote_yaml(job_info, vqpu_id=vqpu_id)
        vqpu_script = await self.__create_vqpu_script(vqpu_id=vqpu_id, vqpu_backend=vqpu_backend)
        cmds = ['bash', vqpu_script]
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

    async def task_walltime(self, walltime : float) -> str:
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

    def checkvqpubackends(
            self, 
            vqpu_id : int, 
            vqpu_backend : str 
            ) -> None:
        """Runs a number of checks of the backend to see if enabled set of backends

        Args:
            vqpu_id (int): id to check
            vqpu_backend (str): backend to check
        """
        if vqpu_backend != None:
            allowed = list(self.vqpu_allowed_backends.keys())
            if vqpu_backend not in allowed:
                message : str = f'vqpu {vqpu_id} set to use {vqpu_backend}, which is not in allowed backends. Please use one of {allowed}'
                raise ValueError(message)


# class HybridQuantumWorkflowSerializer(JSONSerializer):

#     # import types
#     standard_types = [
#     int, float, str, bool, list, tuple, dict, set, complex, 
#     ]
#     """list standard types which the JSONSerializer should be able to handle"""

#     def serialize(self, value : Any) -> Dict:
#         """Return a dictionary. Assumes that classes have a to_dict() function
        
#         Args: 
#             value (Any) : value to serialize to a dictionary 
        
#         Returns:
#             returns a dictionary that JSON serialize would be happy with 
#         """
#         print(type(value))
#         if isinstance(value, HybridQuantumWorkflowBase):
#             return value.to_dict()
#         elif isinstance(value, EventFile):
#             return value.to_dict()
#         # elif type(value) not in self.standard_types:
#         #     return value.to_dict()
#         return super().dumps(value)

#     def deserialize(self, value : Dict) -> Any:
#         """Return an instance of a class or some value
        
#         Args: 
#             value (Dict) : value to deserialize to a an instance of some class 
        
#         Returns:
#             returns an instance of some class or simple object 
#         """
#         try:
#             return HybridQuantumWorkflowBase.from_dict(value)
#         except Exception:
#             try:
#                 return EventFile.from_dict(value)
#             except:
#                 return super().loads(value)

class SillyTestClass:
    """To run unit tests and check flows
    """
    y : int = 0
    def __init__(self, cluster : str | None = None, x : float =2):
        self.x : float = x
        if cluster != None:
            self.cluster = cluster
            taskrunners = get_dask_runners(cluster=self.cluster)
            self.taskrunners = copy.deepcopy(taskrunners['jobscript'])
            # check that taskrunners has minimum set of defined cluster configurations
            clusconfigs = list(self.taskrunners.keys())
            reqclusconfigs = ['generic', 'circuit', 'vqpu', 'cpu', 'gpu']
            valerr = []
            for elem in reqclusconfigs:
                if elem not in clusconfigs:
                    valerr.append(elem)
            if len(valerr) > 0:
                raise ValueError(f'Missing cluster configurations. Minimum set required is {reqclusconfigs}. Missing {valerr}')
        else:
            self.cluster = ''

    def gettaskrunner(self, task_runner_name : str) -> DaskTaskRunner | None:
        if self.cluster == '':
            return None
        runners = get_dask_runners(cluster=self.cluster)
        if task_runner_name not in list(self.taskrunners.keys()):
            raise ValueError(f'Cluster {self.cluster} configuration does not have runner {task-task_runner_name}.')
        return runners[task_runner_name]

# Defining some useful decorators. 
def measure_time(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        end = time.time()
        print(f"Execution time : {end - start:.6f} s")
        return result
    return wrapper
