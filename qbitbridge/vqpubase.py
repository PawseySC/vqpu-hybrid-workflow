"""
@file vqpubase.py
@brief Collection of Classes that help orchestrate hybrid QPU,CPU,GPU workflows

"""

import subprocess, os, psutil, copy
import functools
import json
from pathlib import Path
import importlib
import numpy as np
from typing import (
    List,
    Any,
    Dict,
    NamedTuple,
    Optional,
    Tuple,
    Union,
    Generator,
    Callable,
)
from .clusters import get_dask_runners
from .utils import (
    check_python_installation,
    save_artifact,
    run_a_srun_process,
    run_a_process,
    run_a_process_bg,
    get_task_run_id,
    get_job_info,
    get_flow_runs,
    upload_image_as_artifact,
    SlurmInfo,
    EventFile,
)
import yaml
import asyncio
from prefect_dask import DaskTaskRunner
from prefect import flow, task, get_client, pause_flow_run
from prefect.logging import get_run_logger
from prefect.artifacts import Artifact
from prefect.context import get_run_context, TaskRunContext
from prefect.serializers import Serializer, JSONSerializer


class QPUMetaData:
    """
    A class that contains the basic metadata that a (v)QPU should have.
    We could base it on AWS Braket but want this to be generic so
    we'll use ideas from AWS but keep it separate
    """

    connectivity_types: List[str] = ["all", "linear", ""]
    """Allowed types of connectivity description"""
    noise_types: List[str] = [
        "bitflip",
        "phaseflip",
        "amplitude_dampening",
        "depolarization",
        "dephasing",
        "crosstalk",
        "readout",
    ]
    info_header: str = "# QPU info -"

    def __init__(
        self,
        name: str,
        qubit_type: str,
        qubit_count: int,
        shot_speed: float = 0.0,
        cost: Tuple[float, str] | None = None,
        connectivity: List[str] | None = None,
        gates: List[str] | None = None,
        noise: Dict[str, float] | None = None,
        calibration: Dict | None = None,
    ):
        if qubit_count < 1:
            raise ValueError("Max number of qubits must be 1 or more.")
        self.name = name
        """Name of QPU"""
        self.qubit_type = qubit_type
        """Type of qpu, such as vqpu, superconducting, etc"""
        self.qubit_count = qubit_count
        """max number of qubits"""
        self.shot_speed = shot_speed
        """Operational shot speed in seconds """
        self.cost = (0, "USD/minute")
        """cost to run jobs on device"""
        self.gates = ["all"]
        """Gates allowed"""
        self.connectivity = ["all"]
        """connectivity of qubits"""
        self.noise = {n: 0.0 for n in self.noise_types}
        """noise model"""
        self.calibration = {"Calibration data": None}
        """calibration information """
        if cost is not None:
            self.cost = cost
        if calibration is not None:
            self.calibration = calibration
        if gates is not None:
            self.native_gates = gates
        if connectivity is not None:
            self.connectivity = connectivity

        if noise is not None:
            ntypes = list(noise.keys())
            for nt in ntypes:
                if nt in self.noise_types:
                    self.noise[nt] = noise[nt]
                else:
                    message: str = (
                        f"Uknown noise type {nt}. Please define noise in terms of {ntypes}"
                    )
                    raise ValueError(message)

    def __str__(self) -> str:
        message: str = f"{self.info_header}\n"
        message += f"Name: {self.name}; "
        message += f"Qubit_type: {self.qubit_type}; "
        message += f"Qubit_count: {self.qubit_count}; "
        message += f"Shot_speed: {self.shot_speed}; "
        message += f"Cost: {self.cost}; "
        message += f"Gates: {self.gates}; "
        message += f"Connectivity: {self.connectivity}; "
        message += f"Noise: {self.noise}; "
        message += f"Calibration: {self.calibration}; "
        return message

    def to_dict(self) -> Dict:
        """Converts class to dictionary for serialisation

        Returns:
            Dict containin relevant info. Can be used for serialization
        """
        return {
            "QPUMetaData": {
                "name": self.name,
                "qubit_type": self.qubit_type,
                "qubit_count": self.qubit_count,
                "shot_speed": self.shot_speed,
                "cost": self.cost,
                "gates": self.gates,
                "connectivity": self.connectivity,
                "calibration": self.calibration,
                "noise": self.noise,
            }
        }

    @classmethod
    def from_dict(cls, data: Dict):
        """Create an object from a dictionary

        Args:
            data (dict): input dict

        Raises:
            ValueError if dict does not contain appropriate information

        Returns:
            QPUMetaData instance
        """
        if "QPUMetaData" not in list(data.keys()):
            raise ValueError("Not an QPUMetaData dictionary")
        data = data["QPUMetaData"]
        return cls(
            name=data["name"],
            qubit_type=data["qubit_type"],
            qubit_count=int(data["qubit_count"]),
            shot_speed=float(data["shot_speed"]),
            cost=data["cost"],
            gates=data["gates"],
            connectivity=data["connectivity"],
            calibration=data["calibration"],
            noise=data["noise"],
        )

    @classmethod
    def from_string(cls, arg: str):
        """Create an object from a string

        Args:
            arg (str): input string

        Raises:
            ValueError if string does not contain appropriate information

        Returns:
            QPUMetaData instance
        """
        if cls.info_header not in arg:
            raise ValueError("Not an QPUMetaData string")

        lines = arg.split(cls.info_header + "\n")[1].strip().split(";")[:-1]
        data = dict()
        key: str = ""
        value: str = ""
        for l in lines:
            if "Calibration" not in l and "Noise" not in l:
                [key, value] = l.split(": ")
            elif "Calibration:" in l:
                key, value = "calibration", l.split("Calibration: ")[1]
            elif "Noise:" in l:
                key, value = "noise", l.split("Noise: ")[1]
            else:
                pass
            data[key.lower().lstrip()] = value
        # need to also parse the noise, calibration, connectivity data so that
        # they are not strings
        import ast

        data["qubit_count"] = int(data["qubit_count"])
        data["shot_speed"] = float(data["shot_speed"])
        data["cost"] = data["cost"].replace("price=", "").split(" unit=")
        data["cost"] = (float(data["cost"][0]), data["cost"][1])
        data["gates"] = ast.literal_eval(data["gates"])
        data["connectivity"] = ast.literal_eval(data["connectivity"])
        data["calibration"] = ast.literal_eval(data["calibration"])
        data["noise"] = ast.literal_eval(data["noise"])
        return cls(
            name=data["name"],
            qubit_type=data["qubit_type"],
            qubit_count=data["qubit_count"],
            shot_speed=data["shot_speed"],
            cost=data["cost"],
            gates=data["gates"],
            connectivity=data["connectivity"],
            noise=data["noise"],
            calibration=data["calibration"],
        )

    def __eq__(self, other):
        if isinstance(other, QPUMetaData):
            return self.to_dict() == other.to_dict()
        return False

    def compare(
        self,
        otherqpu,
        allowed_types: List[str] | None = None,
    ) -> bool:
        """
        Compare qpus and return True if current qpu is satisfied by other qpu

        Args:
            otherqpu (QPUMetaData): the other qpu to compare to
            allowed_types (List[str]): other allowed types that can satisfy

        """
        if not isinstance(otherqpu, QPUMetaData):
            raise TypeError("otherqpu not of type QPUMetaData.")
        if self.qubit_count > otherqpu.qubit_count:
            return False
        if allowed_types is None:
            allowed_types = [otherqpu.qubit_type]
        else:
            allowed_types.append(otherqpu.qubit_type)

        if self.qubit_type not in allowed_types:
            return False
        if self.shot_speed < otherqpu.shot_speed:
            return False
        # if connectivity isn't all then need to check
        if ("all" in otherqpu.connectivity) and ("all" not in self.connectivity):
            return False
        elif otherqpu.connectivity != "all" and self.connectivity != "all":
            # need some logic to decide what connectivity is acceptable.
            pass

        for k in self.noise.keys():
            if otherqpu.noise[k] > self.noise[k] == 0:
                return False
        return True


class HybridQuantumWorkflowBase:
    """
    A class that contains basic tasks for running hybrid vQPU workflows.
    """

    reqclusconfigs: List[str] = ["generic", "circuit", "vqpu", "cpu", "gpu"]
    """List of required configurations for running workflow."""
    vqpu_allowed_backends: Dict[str, str] = {
        "qsim": "Description: A noise-aware, GPU-enabled state-vector simulator developed by Quantum Brilliance, built atop the Google Cirq qsim simulator",
        # sparse sim doesn't handle noise, so no reason for vQPU
        # "sparse-sim": "Description: Microsoft Quantum sparse state-vector simulator allows a high number of qubits to be simulated whenever the quantum circuit being executed preserves sparsity. It utilises a sparse representation of the state vector and methods to delay the application of some gates (e.g. Hadamard).",
        # might want to remove qpp as it is not noise aware
        # "qpp": "Description: Quantum++ state vector simulator, configured to use XACC IR",
        # remove all cudaq as not supported (yet)
        # "cudaq:qpp": "Description: Quantum++ state vector simulator using QIR",
        # "custatevec:fp32": "Description: Single (custatevec:fp32) CUDA Quantum state vector simulator, built on CuQuantum libraries.",
        # "custatevec:fp64": "Description: double-precision CUDA Quantum state vector",
        # default
        "aer": "Description: IBM Qiskit Aer noise-aware state-vector (and MPS and density-matrix simulator) [Default]",
        "qb-mps": "Description: Quantum Brilliance noise-aware MPS simulator, configured to use XACC IR (qb-mps). The MPS method represents the quantum wavefunction as a tensor contraction of individual qubit quantum state. Each qubit quantum state is a rank-3 tensor (rank-2 tensor for boundary qubits).",
        # "cudaq:qb_mps": "Description: MPS using QIR.",
        "qb-mpdo": "Description: Quantum Brilliance noise-aware matrix-product density operator (MPDO) simulator, configured to use XACC IR (qb-mpdo). The MPDO method represents the density matrix as a tensor contraction of individual qubit density operator. Each qubit density operator is a rank-4 tensor (rank-3 tensor for boundary qubits).",
        # "cudaq:qb_mpdo": "Description: MPDO using QIR",
        "qb-purification": "Descrption: Quantum Brilliance noise-aware state purification simulator, configured to use XACC IR (qb-purification). The purification method represents the purified quantum state as a tensor contraction of individual qubit purified state. Each qubit purified state is a rank-4 tensor (rank-3 tensor for boundary qubits).",
        # "cudaq:qb_purification": "Description: noise-aware state purification using QIR",
        # "cudaq:dm": "Description: The CUDA Quantum density matrix simulator, built on CuQuantum libraries",
    }
    """List of allowed vqpu backends"""

    vqpu_backend_default: str = "aer"
    """Default vqpu backend simulator"""

    def __init__(
        self,
        cluster: str,
        vqpu_ids: List[int] = [i for i in range(100)],
        name: str = "myflow",
        vqpu_backends: List[str] | None = None,
        backends: List[str] | None = None,
        eventloc: str | None = None,
        vqpu_template_script: str | None = None,
        vqpu_template_yaml: str | None = None,
        vqpu_run_dir: str | None = None,
        vqpu_exec: str | None = None,
        events: Dict[str, EventFile] | None = None,
        active_qpus: Dict[int, QPUMetaData] | None = None,
    ):
        """
        Constructs all the necessary attributes for the person object.

        Args:
            cluster (str): cluster name.
            maxvqpu (int): max number of vqpus to launch
            name (str): name of flowbase
            vqpu_backends (List[str]): set of vqpu_backends required
            backends (List[str]): set of simulator backends
            eventloc (str): location of events during orchestration of workflow
            vqpu_template_script (str): script that is run to setup running of vqpu
            vqpu_template_yaml (str): config yaml that is run to setup running of vqpu
            vqpu_exe (str): executable to run for vqpu service
            events (Dict[str, EventFile]): set of events used to orchestrate workflow
            active_qpus (Dict[int, QPUMetaData]): set of active (v)qpus running
        """
        fpath: str = str(os.path.dirname(os.path.abspath(__file__)))
        self.name: str
        """name of workflow"""
        self.cluster: str
        """cluster name for slurm configurations"""
        self.maxvqpu: int = 100
        """max number of virtual qpus"""
        self.vqpu_template_script: str = f"{fpath}/../workflow/qb-vqpu/vqpu_template.sh"
        """template vqpu start up script to run"""
        self.vqpu_template_yaml: str = (
            f"{fpath}/../workflow/qb-vqpu/remote_vqpu_template.yaml"
        )
        """vqpu remote yaml template"""
        self.vqpu_run_dir: str = (
            f"{os.path.dirname(os.path.abspath(__file__))}/../workflow/vqpus/"
        )
        """directory where to store the active vqpu yamls and scripts"""
        self.vqpu_exec: str = "qcstack"
        """vqpu executable. Default is QB's vQPU executable"""
        self.vqpu_ids: List[int]
        """list of active vqpus"""
        self.vqpu_backends: List[str]
        """list of backend end used of the vqpu (state-vector, density-matrix, MPS)"""
        self.events: Dict[str, EventFile] = dict()
        """list of events"""
        self.eventloc: str = (
            f"{os.path.dirname(os.path.abspath(__file__))}/../workflow/events/"
        )
        """location of where to store event files"""
        # before taskrunners also stored the DaskTaskRunner but this leads to issues
        # with serialization. Now just store the slurm job script
        self.taskrunners: Dict[str, Dict[str, Any]]
        """The slurm configuration stored in the DaskTaskRunner used by Prefect"""
        self.backends: List[str] = [
            "qb-vqpu",
            "braket",
            "quera",
            "qiskit",
            "pennylane",
            "cudaq",
        ]
        """List of allowed quantum circuit backends"""
        self.active_qpus: Dict[int, QPUMetaData] = {}
        """Dictionary of active qpus """

        self.name = name
        self.cluster = cluster
        # note before had the taskrunners store the actual DaskTaskRunner instances but this generated problems with serialization.
        # So instead will store the configuration but not the actual task runner.
        # Now there is a function that will get the appropriate task runner
        # since the get_dask_runners function returns a Dict DaskTaskRunners, the relevant slurm job script
        # and also the specs that can be used to create a dask runner, parse its output
        runners = get_dask_runners(cluster=self.cluster)
        self.taskrunners = {
            "jobscript": copy.deepcopy(runners["jobscript"]),
            "specs": copy.deepcopy(runners["specs"]),
        }

        # check that taskrunners has minimum set of defined cluster configurations
        clusconfigs = list(self.taskrunners["jobscript"].keys())
        valerr = []
        for elem in self.reqclusconfigs:
            if elem not in clusconfigs:
                valerr.append(elem)
        if len(valerr) > 0:
            raise ValueError(
                f"Missing cluster configurations. Minimum set required is {self.reqclusconfigs}. Missing {valerr}"
            )

        if backends is not None:
            self.backends = backends
        # Do I check backends?
        reqbackends = ["qiskit", "pennylane"]
        valerr = []
        for elem in reqbackends:
            if elem not in self.backends:
                valerr.append(elem)
        if len(valerr) > 0:
            raise ValueError(
                f"Missing minimum req backends. Minimum set required is {reqbackends}. Missing {valerr}"
            )

        self.vqpu_ids = vqpu_ids
        # set the default backend. Not clear if this structure is really necessary
        if vqpu_backends is not None:
            self.vqpu_backends = vqpu_backends
            if len(self.vqpu_backends) < len(self.vqpu_ids):
                self.vqpu_backends.append(None)
        else:
            self.vqpu_backends = None

        if eventloc is not None:
            self.eventloc = eventloc
        if vqpu_run_dir is not None:
            self.vqpu_run_dir = vqpu_run_dir
        if vqpu_exec is not None:
            self.vqpu_exec = vqpu_exec
        if vqpu_template_script is not None:
            self.vqpu_template_script = vqpu_template_script
        if vqpu_template_yaml is not None:
            self.vqpu_template_yaml = vqpu_template_yaml
        # this is very odd, don't know why default is being saved as tuple
        if isinstance(self.vqpu_template_yaml, tuple):
            self.vqpu_template_yaml = self.vqpu_template_yaml[0]

        if not os.path.isfile(self.vqpu_template_yaml):
            message: str = (
                f"Template yaml file {self.vqpu_template_yaml} not found. Please ensure file exists."
            )
            raise ValueError(message)
        if not os.path.isfile(self.vqpu_template_script):
            message: str = (
                f"Template vqpu start-up script file {self.vqpu_template_script} not found. Please ensure file exists."
            )
            raise ValueError(message)

        if active_qpus is not None:
            self.active_qpus = copy.deepcopy(active_qpus)

        if events is None:
            if "qb-vqpu" in self.backends:
                for vqpu_id in self.vqpu_ids:
                    self.events[f"qpu_{vqpu_id}_launch"] = EventFile(
                        name=f"qpu_{vqpu_id}_launch", loc=self.eventloc
                    )
                    self.events[f"qpu_{vqpu_id}_circuits_finished"] = EventFile(
                        name=f"qpu_{vqpu_id}_circuits_finished", loc=self.eventloc
                    )
        else:
            # do a deep copy
            self.events = copy.deepcopy(events)

    def __str__(self):
        message: str = f"Hybrid Quantum Workflow {self.name} running on\n"
        message += f"Cluster : {self.cluster}\n"
        for key, value in self.taskrunners["jobscript"].items():
            message += f"Slurm configuration {key}: {value}\n"
        message += f"Allowed QPU backends : {self.backends}\n"
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

        Returns:
            Dict containing relevant info
        """
        eventdict = dict()
        for k, e in self.events.items():
            eventdict[k] = e.to_dict()
        return {
            "HybridQuantumWorkflowBase": {
                "name": self.name,
                "cluster": self.cluster,
                "maxvqpu": self.maxvqpu,
                "vqpu_script": self.vqpu_template_script,
                "vqpu_template_yaml": self.vqpu_template_yaml,
                "vqpu_run_dir": self.vqpu_run_dir,
                "vqpu_exec": self.vqpu_exec,
                "vqpu_ids": self.vqpu_ids,
                "events": eventdict,
                "eventloc": self.eventloc,
                "backends": self.backends,
                "active_qpus": self.active_qpus,
            }
        }

    @classmethod
    def from_dict(cls, data: Dict):
        """Create an object from a dictionary

        Args:
            data (dict): input dictionary

        Raises:
            ValueError if input dictionary does contain appropriate key indicating that it contains appropriate data

        Returns:
            Instance of HybridQuantumWorkflowBase class
        """
        # print('hybridworlfow from_dict', data.keys())
        if "HybridQuantumWorkflowBase" not in list(data.keys()):
            raise ValueError("Not an HybridQuantumWorkflowBase dictionary")
        data = data["HybridQuantumWorkflowBase"]
        eventdict = dict()
        for k, e in data["events"].items():
            eventdict[k] = EventFile.from_dict(e)
        return cls(
            name=data["name"],
            cluster=data["cluster"],
            # maxvqpu=data['maxvpuq'],
            vqpu_template_script=data["vqpu_script"],
            vqpu_template_yaml=data["vqpu_template_yaml"],
            vqpu_run_dir=data["vqpu_run_dir"],
            vqpu_exec=data["vqpu_exec"],
            vqpu_ids=data["vqpu_ids"],
            events=eventdict,
            backends=data["backends"],
            active_qpus=data["active_qpus"],
        )

    def __eq__(self, other) -> bool:
        """Equality operator based on serializing HybridQuantumWorkflowBase as a dict

        Args:
            other: the other object to compare to

        Returns:
            bool, True for equality
        """
        if isinstance(other, HybridQuantumWorkflowBase):
            return self.to_dict() == other.to_dict()
        return False

    async def getqpudata(self, qpu_id: int) -> QPUMetaData:
        """Get the QPU Meta Data for a given qpu_id

        Args:
            qpu_id (int) : The qpu_id to query

        Returns:
            QPUMetaData of the qpu in question
        """
        if qpu_id in list(self.active_qpus.keys()):
            return self.active_qpus[qpu_id]
        # need to retrieve it
        else:
            artifact = await Artifact.get(key=f"activeqpudata{qpu_id}")
            if artifact is None:
                raise ValueError(
                    "asking workflow to get active qpu data but no active qpu found!"
                )
            data = dict(artifact)["data"]
            return QPUMetaData.from_string(data)

    def gettaskrunner(
        self,
        task_runner_name: str,
        extra_cluster_kwargs: Optional[Dict[str, Any]] = None,
    ) -> DaskTaskRunner:
        """Returns the appropriate task runner. This is created based on the specs.

        Args:
            task_runner_name (str): name of dasktaskrunner configuration

        Returns:
            A DaskTaskRunner
        """
        if task_runner_name not in list(self.taskrunners["jobscript"].keys()):
            raise ValueError(
                f"Cluster {self.cluster} configuration does not have runner {task_runner_name}."
            )
        cluster_config = copy.deepcopy(self.taskrunners["specs"][task_runner_name])
        if extra_cluster_kwargs is not None:
            cluster_config["cluster_kwargs"].update(extra_cluster_kwargs)
        return DaskTaskRunner(**cluster_config)
        # return runners[task_runner_name]

    async def __create_vqpu_remote_yaml(
        self, job_info: Union[SlurmInfo], vqpu_id: int
    ) -> None:
        """Saves the remote backend for the vqpu to a yaml file and artifact
        having extracted the hostname running the vqpu from the slurm job

        Args:
            job_info (Union[SlurmInfo]): slurm job information
            vqpu_id (int): The vqpu id
        """
        # where to save the workflow yaml
        workflow_yaml = f"{self.vqpu_run_dir}/remote_vqpu-{vqpu_id}.yaml"
        # here there is an assumption of the path to the template
        lines = open(self.vqpu_template_yaml, "r").readlines()
        fout = open(workflow_yaml, "w")
        # update the HOSTNAME
        for line in lines:
            if "HOSTNAME" in line:
                line = line.replace("HOSTNAME", job_info.hostname)
            fout.write(line)
        # to store the results of this task, make use of a helper function that creates artifcat
        await save_artifact(workflow_yaml, key=f"remote{vqpu_id}")
        await save_artifact(job_info.job_id, key=f"vqpujobid{vqpu_id}")

    def __delete_vqpu_remote_yaml(
        self,
        vqpu_id: int,
    ) -> None:
        """Removes the yaml file of the vqpu

        Args:
            vqpu_id (int): The vqpu id
        """

        workflow_yaml = f"{self.vqpu_run_dir}/remote_vqpu-{vqpu_id}.yaml"
        if os.path.isfile(workflow_yaml):
            os.remove(workflow_yaml)

    async def __create_vqpu_script(
        self,
        vqpu_id: int,
        vqpu_backend: str | None = None,
    ) -> str:
        """Saves the script backend for the vqpu to a yaml file and artifact
        having extracted the hostname running the vqpu from the slurm job

        Args:
            vqpu_id (int): The vqpu id
            vqpu_backend (str): The backend to use for this vqpu
        """
        # where to save the vqpu start up script
        vqpu_script = f"{self.vqpu_run_dir}/vqpu-{vqpu_id}.sh"
        # here there is an assumption of the path to the template
        lines = open(self.vqpu_template_script, "r").readlines()
        fout = open(vqpu_script, "w")
        if vqpu_backend is None:
            vqpu_backend = self.vqpu_backend_default
        self.checkvqpubackends(vqpu_id=vqpu_id, vqpu_backend=vqpu_backend)

        # update the backend
        for line in lines:
            if "MY_VQPU_BACKEND" in line:
                line = line.replace("MY_VQPU_BACKEND", vqpu_backend)
            fout.write(line)
        # to store the results of this task, make use of a helper function that creates artifcat
        await save_artifact(vqpu_script, key=f"vqpuscript{vqpu_id}")
        return vqpu_script

    async def launch_vqpu(
        self,
        job_info: SlurmInfo,
        vqpu_id: int,
        spinuptime: float,
        vqpu_backend: str | None = None,
        vqpu_data: QPUMetaData | None = None,
    ) -> None:
        """Launchs the vqpu service and generates events to indicate it has been launched

        Args:
            job_info (SlurmInfo) : gets the slurm job info related to spinning up the service
            vqpu_id (int) : The vqpu id
            spinuptime (float) : The time to wait before setting the event
            vqpu_data (QPUMetaData) : Optional metadata to pass
        """
        # check if qpu is alread in activated list of qpus

        # now add the vQPU to the active list
        if vqpu_data is None:
            self.active_qpus[vqpu_id] = QPUMetaData(
                name=f"Virtual QPU-{vqpu_id}", qubit_type="vQPU", qubit_count=32
            )
        else:
            self.active_qpus[vqpu_id] = copy.deepcopy(vqpu_data)
        await save_artifact(str(vqpu_id), key=f"activeqpu{vqpu_id}")
        await save_artifact(
            str(self.active_qpus[vqpu_id]), key=f"activeqpudata{vqpu_id}"
        )

        await self.__create_vqpu_remote_yaml(job_info, vqpu_id=vqpu_id)
        vqpu_script = await self.__create_vqpu_script(
            vqpu_id=vqpu_id, vqpu_backend=vqpu_backend
        )

        cmds = ["bash", vqpu_script]
        process = run_a_process(cmds)
        await asyncio.sleep(spinuptime)
        self.events[f"qpu_{vqpu_id}_launch"].set(str(self.active_qpus[vqpu_id]))

    async def shutdown_vqpu(self, vqpu_id: int):
        """Shuts down the vqpu service

        Args:
            vqpu_id (int) : The vqpu id
        """
        await self.events[f"qpu_{vqpu_id}_launch"].wait()
        # could try checking that vqpu is active
        # artifact = await Artifact.get(key=f"activeqpu{vqpu_id}")
        # if artifact is None:
        #     message: str = f"QPU {vqpu_id} not active. Cannot shutdown. Terminating"
        #     raise RuntimeError(message)

        # kill the process
        for proc in psutil.process_iter():
            # check whether the process name matches
            if proc.name() == self.vqpu_exec:
                proc.kill()

        # now clean-up
        self.__delete_vqpu_remote_yaml(vqpu_id)
        # delete the active qpu data if present
        if vqpu_id in list(self.active_qpus.keys()):
            del self.active_qpus[vqpu_id]

    async def launch_qpu(
        self,
        qpu_id: int,
        remote_query: Tuple[str | Callable, str] | None = None,
        qpu_data: QPUMetaData | None = None,
        remote_data_query: Tuple[str | Callable, str] | None = None,
    ) -> None:
        """Launchs qpu service (that is register the service) and generates events to indicate that qpu is available.

        Args:
            qpu_id (int) : The qpu id
            remote_query (Tuple): The command(s)/function call to run to see if remote is available. First entry is command to run. If it is a string running as a subprocess, result should be Yes/No to standard out. If callable function, it should return a bool. Second tuple argument is arguments to pass.
            qpu_data (QPUMetaData): The qpu's metadata (provided explicitly)
            remote_query (Tuple): The command(s)/function call to run to get metadata and arguments to pass. First entry is command to run. If it is a string running as a subprocess, stdout is captured to a yaml file. If callable function, it should return produce a yaml file. Second tuple argument is arguments to pass.

        Raises:
            ValueError if qpu_data or remote_query not passed as there must be a way of gathering meta_data about the qpu
        """
        # now add the vQPU to the active list
        if qpu_data is None and remote_data_query is None:
            raise ValueError(
                "QPU missing meta data either provided explicitly or remote access to metadata"
            )

        # check if qpu is alread in activated list of qpus
        avail: bool = True
        if remote_query is not None:
            if isinstance(remote_query[0], str):
                cmds = [remote_query[0]]
                cmds += [remote_query[1].split(" ")]
                process = subprocess.Popen(cmds, text=True)
                if process.stdout == "No":
                    avail = False
            else:
                avail = remote_query[0](args=remote_query[1], filename=fname)
        if not avail:
            message: str = ""
            if qpu_data is not None:
                message += f"Requested {qpu_data} under qpu-{qpu_id}."
            message += f"Not available."
            raise RuntimeError(message)

        if qpu_data is not None:
            self.active_qpus[qpu_id] = copy.deepcopy(qpu_data)
        else:
            # need to determine how to setup remote access for the QPU. Ideally there is a simple command to run
            # the command(s) should be split by , and pipe results to a file
            fname: str = f"meta_data_for_{qpu_id}.yaml"
            if isinstance(remote_data_query[0], str):
                cmds = [remote_data_query[0]]
                cmds += [remote_data_query[1].split(",")]
                metafile = open(fname, "w")
                process = subprocess.Popen(cmds, stdout=metafile, text=True)
            else:
                remote_data_query[0](args=remote_data_query[1], filename=fname)

            # convert metadata file produced
            metadata = yaml.load(fname, Loader=yaml.Loader)
            self.active_qpus[qpu_id] = QPUMetaData(
                name=metadata["name"],
                qubit_type=metadata["type"],
                qubit_count=metadata["qubit_count"],
            )
        await save_artifact(str(qpu_id), key=f"activeqpu{qpu_id}")
        await save_artifact(str(self.active_qpus[qpu_id]), key=f"activeqpudata{qpu_id}")
        self.events[f"qpu_{qpu_id}_launch"].set(meta_data=str(self.active_qpus[qpu_id]))

    async def shutdown_qpu(self, qpu_id: int):
        """Shuts down the qpu service (and indicate qpu is unavailable to workflow)

        Args:
            qpu_id (int) : The qpu id
        """
        # @todo need to update events so that the qpu event can be created in workflow constructor
        await self.events[f"qpu_{qpu_id}_launch"].wait()
        # could try checking that vqpu is active
        # artifact = await Artifact.get(key=f"activeqpu{vqpu_id}")
        # if artifact is None:
        #     message: str = f"QPU {vqpu_id} not active. Cannot shutdown. Terminating"
        #     raise RuntimeError(message)

        # delete the active qpu data if present
        if qpu_id in list(self.active_qpus.keys()):
            del self.active_qpus[qpu_id]

    def cleanupbeforestart(self, vqpu_id: int | None = None):
        if vqpu_id is None:
            for vqpu_id in self.vqpu_ids:
                self.events[f"qpu_{vqpu_id}_launch"].clean()
                self.__delete_vqpu_remote_yaml(vqpu_id)
        else:
            self.events[f"qpu_{vqpu_id}_launch"].clean()
            self.__delete_vqpu_remote_yaml(vqpu_id)

    async def task_circuitcomplete(self, vqpu_id: int) -> str:
        """Async task that indicates why vqpu can proceed to be shutdown as all circuits have been run

        Args:
            vqpu_id (int): The vqpu id

        Returns:
            returns a message of what has completed
        """
        await self.events[f"qpu_{vqpu_id}_circuits_finished"].wait()
        return "Circuits completed!"

    async def task_walltime(self, walltime: float) -> str:
        """Async task that indicates why vqpu can proceed to be shutdown as has exceeded walltime

        Args:
            walltime (float): Walltime to wait before shutting down vqpu
            vqpu_id (int): The vqpu id
        Returns:
            returns a message of what has completed
        """
        await asyncio.sleep(walltime)
        return "Walltime complete"

    async def task_check_available(
        self, qpu_id: int, check: Callable, arguments: Any, sampling: float = 10.0
    ) -> str:
        """Async task that indicates qpu is no longer available

        Args:
            qpu_id (int): The qpu id
            check (Callable): the function to call to see if device is available
            arguments (Any): arguments to pass
            sampling (float): How often to check if device available

        Returns:
            returns a message of what has completed
        """

        while await check(arguments):
            await asyncio.sleep(sampling)
        # await self.events[f"qpu_{qpu_id}_circuits_finished"].wait()
        return "Offline"

    def getcircuitandpost(self, c: Any) -> Tuple:
        """process a possible circuit and any post processing to do. Expecting Callable | Tuple[Callable, Callable] | Tuple[Callable, QPUMetaData] | Tuple[Tuple[Callable, QPUMetaData], Callable]

        Args:
            c : possible combintation of circuit, circuit and post, circuit and circuit requirents, or circuit,circuit requirements and post

        Raises:
            TypeErrors if the type is not correct

        Returns:
            Callable circuit
            Callable post processing (or None)
            QPUMetaData circuit requirements (or None)
        """

        if not (isinstance(c, Callable) or isinstance(c, Tuple)):
            errmessage: str = f"Expected Callable or Tuple but got type {type(c)}"
            raise TypeError(errmessage)
        # Format c
        circ = c
        post = None
        circ_qpu_reqs = None
        if isinstance(c, Tuple):
            # unpack the tuple and store the possible postprocessing call
            circ, post = c
            # check if circ is a Tuple and if so unpack to get circuit and circuit requirements
            if isinstance(circ, Tuple):
                circ, circ_qpu_reqs = circ
            # if circ was not a tuple then expectation would be either Tuple[Callable,Callable] or Tuple[Callable, QPUMetaData]
            elif isinstance(post, QPUMetaData):
                circ_qpu_reqs = post
                post = None

        if not isinstance(circ, Callable):
            errmessage: str = f"Circuit passed but got {type(circ)} instead of Callable"
            raise TypeError(errmessage)

        if not isinstance(post, Callable) and post is not None:
            errmessage: str = (
                f"Postprocessing passed but got {type(post)} instead of Callable"
            )
            raise TypeError(errmessage)

        if not isinstance(circ_qpu_reqs, QPUMetaData) and circ_qpu_reqs is not None:
            errmessage: str = (
                f"Circuit requirements passed but got {type(circ_qpu_reqs)} instead of QPUMetaData"
            )
            raise TypeError(errmessage)

        return circ, post, circ_qpu_reqs

    async def getremoteaftervqpulaunch(
        self,
        vqpu_id: int,
    ) -> str:
        """Waits for a vqpu launch event and then returns the path to the yaml file that defines access to the remote service

        Args:
            vqpu_id (int): vqpu_id to wait for and get remote of
        """
        await self.events[f"qpu_{vqpu_id}_launch"].wait()
        artifact = await Artifact.get(key=f"remote{vqpu_id}")
        remote = dict(artifact)["data"].split("\n")[1]
        return remote

    def checkcircuitreqs(
        self,
        circuitfunc: Callable,
        qpu_id: int,
        circ_qpu_reqs: QPUMetaData | None = None,
    ) -> None:
        """Check that circuit can be run with available qpu

        Args:
            circuitfunc (Callable): circuit to be run
            qpu_id (int): if of (v)QPU that needs to be checked against circuit requirements.
            circ_qpu_reqs (QPUMetaData): requirements. If None, no exception raised.

        Raises:
            RuntimeError if circuit cannot be run on qpu"""
        # check that a circuit can be run with available vqpu
        if circ_qpu_reqs is not None:
            qpu = asyncio.run(self.getqpudata(qpu_id))
            if not circ_qpu_reqs.compare(qpu):
                message: str = (
                    f"Circuit {circuitfunc.__name__} has requirements not satisfied by vQPU. Cannot run. \n Requirements: {circ_qpu_reqs}\n Available: {qpu}\n"
                )
                raise RuntimeError(message)

    def checkbackends(
        self, backend: str = "qb-vqpu", checktype: str = "launch"
    ) -> None:
        """Runs a number of checks of the backend to see if enabled set of backends

        Args:
            backend (str): backend to check
            checktype (str): whether checking launch or circuits or other types
        """
        if backend == "":
            return
        if checktype == "launch":
            if backend == "qb-vqpu" and "qb-vqpu" not in self.backends:
                raise RuntimeError(
                    f"vQPU requested to be launched yet qb-vqpu not in list of acceptable backends. Allowed backends {self.backends}. Terminating"
                )
            elif backend not in self.backends:
                raise RuntimeError(
                    f"{backend} requested to be used but not in list of acceptable backends. Allowed backends {self.backends}. Terminating"
                )
        if checktype == "circuit":
            if backend not in self.backends:
                raise RuntimeError(
                    f"Circuits running using unknown backend {backend}. Allowed backends: {self.backends}. Terminating"
                )

    def checkvqpuid(self, vqpu_id: int) -> None:
        """Checks if vqpu_id in allowed ids

        Args:
            vqpu_id (int): id to check
        """
        if vqpu_id not in self.vqpu_ids:
            raise RuntimeError(
                f"vQPU ID {vqpu_id} requested yet not in allowed list of ids. Allowed ids {self.vqpu_ids}. Terminating"
            )
        pass

    def checkvqpubackends(self, vqpu_id: int, vqpu_backend: str) -> None:
        """Runs a number of checks of the backend to see if enabled set of backends

        Args:
            vqpu_id (int): id to check
            vqpu_backend (str): backend to check
        """
        if vqpu_backend is not None:
            allowed = list(self.vqpu_allowed_backends.keys())
            if vqpu_backend not in allowed:
                message: str = (
                    f"vqpu {vqpu_id} set to use {vqpu_backend}, which is not in allowed backends. Please use one of {allowed}"
                )
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
    """To run unit tests and check flows"""

    y: int = 0

    def __init__(self, cluster: str | None = None, x: float = 2):
        """Constructor"""
        self.x: float = x
        """ just a float """
        if cluster is not None:
            self.cluster = cluster
            taskrunners = get_dask_runners(cluster=self.cluster)
            self.taskrunners = copy.deepcopy(taskrunners["jobscript"])
            # check that taskrunners has minimum set of defined cluster configurations
            clusconfigs = list(self.taskrunners.keys())
            reqclusconfigs = ["generic", "circuit", "vqpu", "cpu", "gpu"]
            valerr = []
            for elem in reqclusconfigs:
                if elem not in clusconfigs:
                    valerr.append(elem)
            if len(valerr) > 0:
                raise ValueError(
                    f"Missing cluster configurations. Minimum set required is {reqclusconfigs}. Missing {valerr}"
                )
        else:
            self.cluster = ""

    def gettaskrunner(self, task_runner_name: str) -> Optional[DaskTaskRunner]:
        """Gets a dask task runner

        Args:
            task_runner_name (str): name of task runner

        Raises:
            ValueError if taskrunner is not in list

        Returns:
            The taskurnner or if there is no cluster and thus no task runners, return None
        """

        if self.cluster == "":
            return None
        runners = get_dask_runners(cluster=self.cluster)
        if task_runner_name not in list(self.taskrunners.keys()):
            raise ValueError(
                f"Cluster {self.cluster} configuration does not have runner {task-task_runner_name}."
            )
        return runners[task_runner_name]
