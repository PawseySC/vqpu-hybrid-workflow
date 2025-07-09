"""
@brief This example shows how to run gpu-accelerated or MPI-enabled quantum simulation to estimate the noise
of quantum hardware.

"""

import sys, os, re
from pathlib import Path
from time import sleep
import datetime
from typing import List, Set, Callable, Tuple, Dict, Any
import warnings

# import qbitbridge
sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/../../")
from qbitbridge.options import vQPUWorkflow
from qbitbridge.vqpubase import HybridQuantumWorkflowBase
from qbitbridge.vqpufitting import (
    LikelihoodModel,
    LikelihoodModelRuntime,
    model_fit_and_analyse_workflow,
    multi_model_flow,
)
from qbitbridge.utils import EventFile, save_artifact, upload_image_as_artifact
from workflow.circuits.qristal_circuits import simulator_setup, noisy_circuit
import asyncio
from prefect import task, flow
from prefect_dask import DaskTaskRunner
from prefect.logging import get_run_logger
import numpy as np
import h5py


def linear_model(X, theta) -> np.ndarray:
    y = X * theta[0] + theta[1]
    return y


def parabolic_model(X, theta) -> np.ndarray:
    y = X * X * theta[0] + X * theta[1] + theta[2]
    return y


# need to fix
def hyperfit(X, theta) -> np.ndarray:
    y = X * theta[0] + theta[1]
    return y


def log_likelihood(
    theta: np.ndarray, model: Callable, X: Any, y: np.ndarray
) -> np.float64:
    """Log-likelihood function

    Args:
        theta (np.ndarray) : parameters
        model (Callable) : model function to evaluate
        X (Any) : input
        y (np.ndarray) : expected output

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
    lgprior: np.float64 = np.float64(0.0)
    return lgprior


# Log-probability
def log_probability(theta: np.ndarray, model: Callable, X: Any, y: np.ndarray) -> Any:
    """Log probability

    Args:
        theta (np.ndarray) : parameters
        model (Callable) : model function to evaluate
        X (Any) : input
        y (np.ndarray) : expected output

    Returns:
        np.float64 of the log probability
    """
    lp = log_prior(theta)
    if not np.isfinite(lp):
        return -np.inf
    return lp + log_likelihood(theta, model, X, y)


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
    model_info["Linear"] = LikelihoodModel(
        name="Linear",
        dask_runner="cpu",
        model_func=linear_model,
        model_dim=2,
        log_prob=log_probability,
        filename="simple_data.h5",
        data_keys=("x", "y"),
        param_labels=["m", "b"],
    )
    model_info["Parabolic"] = LikelihoodModel(
        name="Parabolic",
        dask_runner="gpu",
        model_dim=3,
        model_func=parabolic_model,
        log_prob=log_probability,
        filename="simple_data.h5",
        data_keys=("x", "y"),
        param_labels=[r"$\alpha$", r"$\beta$", r"$\gamma$"],
    )
    # model_info["Hyperfit"] = LikelihoodModel(
    #     name="Hyperfit",
    #     dask_runner="cpu",
    #     model_func=hyperfit,
    #     model_dim=3,
    #     log_prob=log_probability,
    #     filename="simple_data.h5",
    #     data_keys=("x", "y"),
    #     param_labels=[r"$\alph$", r"$\beta$", r"$\sigma_y$"],
    # )
    asyncio.run(multi_model_flow(myflow=myflow, model_info=model_info))


def make_simple_data(fname: str = "simple_data.h5"):
    # Choose the "true" parameters.
    m_true = -0.9594
    b_true = 4.294

    # Generate some synthetic data from the model.
    N = 50
    x = np.sort(10 * np.random.rand(N))
    yerr = 0.1 + 0.5 * np.random.rand(N)
    y = m_true * x + b_true
    y += yerr * np.random.randn(N)

    with h5py.File(fname, "w") as h5f:
        h5f.create_dataset("x", data=x)
        h5f.create_dataset("y", data=y)
        h5f.create_dataset("yerr", data=yerr)


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

    make_simple_data()

    wrapper_to_async_flow(
        yaml_template=yaml_template,
        script_template=script_template,
        cluster=cluster,
    )
