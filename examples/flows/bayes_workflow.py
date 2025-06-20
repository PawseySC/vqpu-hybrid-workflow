"""
@brief This example shows how to run gpu-accelerated or MPI-enabled quantum simulation to estimate the noise
of quantum hardware. 

"""

import sys, os, re
from pathlib import Path
# import qbitbridge
sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/../../")
# import circuits
from time import sleep
import datetime
from typing import List, Set, Callable, Tuple, Dict, Any
from qbitbridge.options import vQPUWorkflow
from qbitbridge.vqpubase import HybridQuantumWorkflowBase
from qbitbridge.vqpuflow import (
    launch_vqpu_workflow,
    circuits_with_nqvpuqs_workflow,
    circuits_vqpu_workflow,
    cpu_workflow,
    gpu_workflow,
    postprocessing_histo_plot,
    run_cpu,
    run_circuits_once_vqpu_ready,
)
from qbitbridge.utils import EventFile, save_artifact, upload_image_as_artifact
from workflow.circuits.qristal_circuits import simulator_setup, noisy_circuit
import asyncio
from prefect import task, flow
from prefect_dask import DaskTaskRunner
from prefect.logging import get_run_logger
import numpy as np
import h5py
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.preprocessing import PolynomialFeatures
from sklearn.pipeline import make_pipeline
from sklearn.metrics import mean_squared_error
import emcee
import corner

class ExampleModel:
    """model object that summarise properties of the model and how to evalate it
    """
    def __init__(self, 
                 name:str, 
                 dask_runner:str, 
                 model_func : Callable, 
                 ref : np.array | None = None, 
                 filename:str | None = None, 
                 data_keys : Tuple[str, str] | None = None, 
                 param_labels: List[str] | None = None,
                 param_priors : List[Callable] | None = None, 
                 params : np.array | None = None, 
                 X : np.array | None = None, 
                 ) -> None:
        self.name = name
        """model name"""
        self.dask_runner = dask_runner 
        """name of dask task runner to use"""
        self.model_func = model_func
        """Function to call"""
        self.ref = ref
        """reference model output"""
        self.filename = filename
        """associated input data file, if any"""
        self.data_keys = data_keys
        """data keys for the HDF5 file if any"""
        self.param_labels = param_labels
        """labels of the model parameters"""
        self.param_priors = param_priors
        """priors to call on parameters """
        self.params = params
        """value of parameters"""
        self.X = X
        """data"""
        
        if self.filename is not None:
            if self.data_keys is None:
                raise ValueError("Provided input filename, must also provide data keys")
            self.load_data()

    def load_data(self) :
        """
        Load data from HDF5, may want to alter this to MPI enabled h5py load 

        Returns:
            inputs and output return
        """
        
        with h5py.File(self.filename, 'r') as f:
            self.X = np.array(f[self.data_keys[0]])
            self.ref = np.array(f[self.data_keys[1]])

def linear_model(X, theta) -> np.array:
    y = X*theta[0] + theta[1]
    return y

def parabolic_model(X, theta) -> np.array:
    y = X*X*theta[0] + X*theta[1] + theta[2]
    return y


def log_likelihood(
        theta : np.array, 
        model : Callable, 
        X: Any, 
        y : np.array
        ) -> np.float64:
    """Log-likelihood function
    
    Args:
        theta (np.array) : parameters 
        model (Callable) : model function to evaluate
        X (Any) : input
        y (np.array) : expected output

    Returns:
        np.float64 of the log likelihood
    """
    y_pred = model(X, theta)
    sigma2 = np.var(y - y_pred)
    return -0.5 * np.sum((y - y_pred) ** 2 / sigma2 + np.log(2 * np.pi * sigma2))

# Log-prior
def log_prior(theta: Any) -> np.float64:
    """Log prior
    Args:
        theta (Any) : parameters of the model

    Returns:
        float of the log prior
    """
    lgprior = 0.0
    return lgprior

# Log-probability
def log_probability(
        theta : np.array, 
        model : Callable, 
        X: Any, 
        y : np.array
        ) -> np.float64:
    """Log probability

    Args:
        theta (np.array) : parameters 
        model (Callable) : model function to evaluate
        X (Any) : input
        y (np.array) : expected output

    Returns:
        np.float64 of the log probability
    """
    lp = log_prior(theta)
    if not np.isfinite(lp):
        return -np.inf
    return lp + log_likelihood(theta, model, X, y)

@task 
def run_sampler(
    model: Callable, 
    X: Any, y: np.array, 
    log_prob: Callable, 
    nwalkers: int = 32, 
    nsteps: int = 5000, 
    init_pos : np.array | None = None, 
    show_progress : bool = False, 
    ):
    # initialize walkers 
    ndim = X.shape
    if init_pos is None:
        init_pos = np.random.randn(nwalkers, ndim)
    else:
        nwalkers = init_pos.shape
    # setup the sampler
    sampler = emcee.EnsembleSampler(
        nwalkers, ndim, 
        log_prob, 
        args=(model, X, y)
        )
    # run the sampler 
    sampler.run_mcmc(init_pos, nsteps, progress=show_progress)
    return sampler

@task 
def estimate_evidence(sampler : emcee.EnsembleSampler) -> float:
    """Estimate Bayesian evidence using emcee
    """
    log_probs = sampler.get_log_prob(flat=True)
    log_evidence = np.logaddexp.reduce(log_probs) - np.log(len(log_probs))
    return log_evidence


@task 
async def corner_plot(flat_samples : np.array, 
                filename : str,
                labels : List[str], 
                truths : np.array | None = None
                ):
    fig = corner.corner(
        flat_samples, 
        labels=labels, 
        truths=truths
    )
    fig.savefig(f"{filename}.png")
    # save the figure so that it can be directly viewed from the Prefect UI
    await upload_image_as_artifact(Path(f"{filename}.png"))



@flow(
    name="Example Model Estimation flow",
    flow_run_name="model_estimation-{date:%Y-%m-%d:%H:%M:%S}",
    description="Running tasks that get the bayes evidence and optimal parameters of a model",
    retries=3,
    retry_delay_seconds=10,
    log_prints=True,
)
async def fit_model_workflow(
    myflow : HybridQuantumWorkflowBase, 
    model : ExampleModel,
    ):
    logger = get_run_logger()
    logger.info(f"Running sampler")
    future = run_sampler.submit(
        model=model.model_func,
        X = model.X, 
        y = model.ref, 
        log_prob = log_probability, 
    )
    sampler = future.result()
    logger.info(f"Estimating evidence for {model.name} model...")
    logZ = estimate_evidence(sampler)
    tau = sampler.get_autocorr_time()
    evidence = logZ
    logger.info(f"{model.name} log evidence: {logZ:.2f}")
    # get new flatten sampels 
    flat_samples = sampler.get_chain(discard=np.average(tau)*3, thin=15, flat=True)

@flow(
    name="Example Multi-Model Estimation flow",
    flow_run_name="model_estimation-{date:%Y-%m-%d:%H:%M:%S}",
    description="Running CPU/GPU flows that get the bayes evidence and optimal model",
    retries=3,
    retry_delay_seconds=10,
    log_prints=True,
)
async def workflow(
    myflow : HybridQuantumWorkflowBase, 
    model_info : Dict[str, ExampleModel], 
    ) -> None:
    """Multi-model workflow
    
    Args:
        model_info (Dict): A dictionary with model name indicating filename, whether to read the data, compute resources, labels of 
    """
    logger = get_run_logger()

    model_flows = dict()
    for k in model_info.keys():
        m = model_info[k]
        model_flows[k] = fit_model_workflow.with_options(task_runner = myflow.gettaskrunner(m.dask_runner))


    tasks = dict()
    async with asyncio.TaskGroup() as tg:
        for k in model_info.keys():
            tasks[k].append(
                tg.create_task(model_flows[k](
                    myflow = myflow,
                    model = model_info[k],
                    )
                )
            )

    logger.info("Finished running models")


def wrapper_to_async_flow(
    yaml_template: str | None = None,
    script_template: str | None = None,
    cluster: str | None = None,
):
    """
    @brief run the workflow with the appropriate task runner
    """
    if yaml_template == None:
        yaml_template = f"{os.path.dirname(os.path.abspath(__file__))}/../../workflow/qb-vqpu/remote_vqpu_ella_template.yaml"
    if script_template == None:
        script_template = f"{os.path.dirname(os.path.abspath(__file__))}/../../workflow/qb-vqpu/vqpu_template_ella_qpu-1.7.0.sh"
    if cluster == None:
        cluster = "ella-qb-1.7.0-pypath"
    myflow = HybridQuantumWorkflowBase(
        cluster=cluster,
        vqpu_ids=[1, 2, 3, 16],
        vqpu_template_yaml=yaml_template,
        vqpu_template_script=script_template,
        eventloc=f"{os.path.dirname(os.path.abspath(__file__))}/events/",
    )
    model_info = dict()
    model_info["Linear"] = ExampleModel(name = "Linear", 
                                        dask_runner="CPU", 
                                        model_func=linear_model, 
                                        filename="simple_data", 
                                        data_keys=("input", "output"),
                                        param_labels=["m", "b"] 
                                        )
    model_info["Parabolic"] = ExampleModel(name = "Parabolic", 
                                        dask_runner="GPU", 
                                        model_func=parabolic_model, 
                                        filename="simple_data", 
                                        data_keys=("input", "output"),
                                        param_labels=["a", "b", "c"] 
                                        )

    asyncio.run(
        workflow(
            myflow=myflow,
            model_info = model_info
        )
    )


if __name__ == "__main__":
    yaml_template = None
    script_template = None
    cluster = None
    res = [i for i in sys.argv if re.findall("--yaml=", i)]
    if len(res) > 0:
        yaml_template = res[0].split("=")[1]
    res = [i for i in sys.argv if re.findall("--script=", i)]
    if len(res) > 0:
        script_template = res[0].split("=")[1]
    res = [i for i in sys.argv if re.findall("--cluster=", i)]
    if len(res) > 0:
        cluster = res[0].split("=")[1]
    wrapper_to_async_flow(
        yaml_template=yaml_template,
        script_template=script_template,
        cluster=cluster,
    )
