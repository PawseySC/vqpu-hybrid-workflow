"""
@brief This example shows how to run gpu-accelerated or MPI-enabled quantum simulation to estimate the noise
of quantum hardware.

"""

import sys, os, re
from pathlib import Path
from time import sleep
import datetime
from typing import List, Set, Callable, Tuple, Dict, Any

# import qbitbridge
sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/../../")
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
import matplotlib.pyplot as plt
import emcee
import corner


class LikelihoodModel:
    """model object that summarise properties of the model and how to evalate it"""

    def __init__(
        self,
        name: str,
        dask_runner: str,
        model_func: Callable,
        ref: np.ndarray | None = None,
        filename: str | None = None,
        data_keys: Tuple[str, str] | None = None,
        param_labels: List[str] | None = None,
        param_priors: List[Callable] | None = None,
        params: np.ndarray | None = None,
        X: np.ndarray | None = None,
        xerr: np.ndarray | None = None,
        yerr: np.ndarray | None = None,
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
        self.data_ndim: int = 1
        """dimensions of the input data"""
        self.Xlims: np.ndarray = None
        """limits of the data"""

        self.xerr = xerr
        """error on input data"""
        self.yerr = yerr
        """error on output reference model data"""

        if self.filename is not None:
            if self.data_keys is None:
                raise ValueError("Provided input filename, must also provide data keys")
            else:
                self.load_data()

        self.data_ndim = self.X.ndim
        np.zeros([self.data_ndim, 2])
        for i in range(self.data_ndim):
            self.Xlims[i][:] = np.array(
                [np.min(self.X[:, :, i]), np.max(self.X[:, :, i])]
            )

    def load_data(self):
        """
        Load data from HDF5, may want to alter this to MPI enabled h5py load

        Returns:
            inputs and output return
        """

        with h5py.File(self.filename, "r") as f:
            self.X = np.ndarray(f[self.data_keys[0]])
            self.ref = np.ndarray(f[self.data_keys[1]])


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
    lgprior = 0.0
    return lgprior


# Log-probability
def log_probability(
    theta: np.ndarray, model: Callable, X: Any, y: np.ndarray
) -> np.float64:
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


@task(
    retries=5,
    retry_delay_seconds=2,
    timeout_seconds=600,
    task_run_name="run_sampler-{model.name}",
)
def model_run_sampler(
    model: LikelihoodModel,
    log_prob: Callable,
    nwalkers: int = 32,
    nsteps: int = 5000,
    init_pos: np.ndarray | None = None,
    show_progress: bool = False,
) -> emcee.EnsembleSampler:
    """Run emcee sampler

    Args:
        model (LikelihoodModel) : the model class that contains relevant info
        log_prob (Callable) : log probability call (log likelihood and prior)
        nwalkers (int) : number of samplers
        nsteps (int) : number of steps to take
        init_pos (np.ndarray) : initial position of the walkers
        show_progress (bool) : show the progress of the sampler

    Returns:
        EnsembleSampler that has sampled the model
    """
    # initialize walkers
    ndim = model.X.shape[0]
    if init_pos is None:
        init_pos = np.random.randn(nwalkers, ndim)
    else:
        nwalkers = init_pos.shape
    # setup the sampler
    sampler = emcee.EnsembleSampler(
        nwalkers, ndim, log_prob, args=(model.model_func, model.X, model.ref)
    )
    # run the sampler
    sampler.run_mcmc(init_pos, nsteps, progress=show_progress)
    return sampler


@task(
    retries=5,
    retry_delay_seconds=2,
    timeout_seconds=600,
    task_run_name="estimate_evidence-{name}",
)
def model_estimate_evidence(name: str, sampler: emcee.EnsembleSampler) -> float:
    """Estimate Bayesian evidence using emcee

    Args:
        name (str) : model name,
        sampler (EnsembleSampler): the sampler used to get the evidence

    Returns:
        float of the evidence
    """
    logger = get_run_logger()
    logger.info(f"Estimating evidence for {name} model...")
    log_probs = sampler.get_log_prob(flat=True)
    log_evidence = np.logaddexp.reduce(log_probs) - np.log(len(log_probs))
    logger.info(f"Model {name} log evidence: {log_evidence:.2f}")
    return log_evidence


@task(
    retries=5,
    retry_delay_seconds=2,
    timeout_seconds=600,
    task_run_name="flatten_sampler",
)
def model_flatten_sampler(
    sampler: emcee.EnsembleSampler,
    nthin: int | None = None,
    nburnin: int | None = None,
) -> np.ndarray:
    """Return flatten array from the sampler given a burnin and thinning.

    Args:
        sampler (EnsembleSampler): the sampler used to get the evidence
        nthin (int) : how much to thin by (default uses max of autocorr time of all the parameters)
        nburnin (int) : burn in

    Returns:
        float of the evidence
    """
    # get the autocorrelation number of steps for all model parameters
    tau = sampler.get_autocorr_time()
    # take the max/2
    if nthin is None:
        nthin = np.max(tau) / 2
    if nburnin is None:
        nburnin = 100
    flat_samples = sampler.get_chain(discard=nburnin, thin=nthin, flat=True)
    return flat_samples


@task(
    retries=5,
    retry_delay_seconds=2,
    timeout_seconds=600,
    task_run_name="report_fit-{name}",
)
async def model_report_fit(
    name: str,
    flat_samples: np.ndarray,
    evidence: float,
    labels: List[str] | None = None,
    quantiles: List[float] | None = None,
    fit_filename: str | None = None,
) -> np.ndarray:
    """Report the quantiles of the model parameters

    Args:
        name (str) : model name
        flat_samples (np.ndarray): the flatten samples
        labels (List[str]) : parameter labels
        quantiles (List[float]) : quantile list with default being [16, 50, 84]

    Returns:
        np.ndarray of the (param, quantiles)
    """
    logger = get_run_logger()
    logger.info(f"Getting quantiles for model {name} ... ")
    ndim = flat_samples.shape[1]
    if labels is None:
        labels = [f"param-{i}" for i in range(ndim)]
    if quantiles is None:
        quantiles = [16.0, 50.0, 84.0]
    else:
        quantiles = np.sort(quantiles)
    results = np.zeros((ndim, len(quantiles)))
    info: str = f"# Model {name}\n"
    info: str = f"Evidence {evidence}\n"
    for i in range(ndim):
        # will need to generalize this eventually
        mcmc = np.percentile(flat_samples[:, i], quantiles)
        results[i] = mcmc[:]
        q = np.diff(mcmc)
        txt = "{3} = {0:.3f}_{{-{1:.3f}}}^{{{2:.3f}}}"
        txt = txt.format(mcmc[1], q[0], q[1], labels[i])
        logger.info(f"")
        info += txt + "\n"
    logger.info(info)
    if fit_filename is not None:
        # @todo need to add check that fit_filename can be created.
        with open(fit_filename, "w") as f:
            f.write(info)

    # strip name should be all lower case with no special characters
    stripname = name.lower().strip("-").strip("_").strip(" ")
    await save_artifact(info, key=stripname)
    return results


@task(
    retries=5,
    retry_delay_seconds=2,
    timeout_seconds=600,
    task_run_name="save_chain-{model.name}",
)
async def model_save_chain(
    model: LikelihoodModel,
    filename: str,
    flat_samples: np.ndarray,
    evidence: float,
    quantiles: List[float] = [16.0, 50.0, 84.0],
    fit_values: np.ndarray | None = None,
) -> None:
    import h5py

    labels = model.param_labels
    ndim = flat_samples.shape[1]
    if labels is None:
        labels = [f"param-{i}" for i in range(ndim)]
    if fit_values is None:
        fitvalues = await model_report_fit.fn(
            name=model.name,
            flat_samples=flat_samples,
            labels=model.param_labels,
            quantiles=quantiles,
        )

    with h5py.File(filename, "w") as h5f:
        with h5f.create_group("Model") as modgrp:
            modgrp["Name"] = model.name
            modgrp.attrs["NParam"] = ndim
            modgrp.attrs["Evidence"] = evidence
            modgrp.create_dataset("Params", data=labels)
            modgrp.create_dataset("Fit", data=fit_values)
            with modgrp.create_group("Likelihood_Chain") as grp:
                for i in range(ndim):
                    data = flat_samples[:, i]
                    grp.create_dataset(labels[i], data=data)


@task(
    retries=5,
    retry_delay_seconds=2,
    timeout_seconds=600,
    task_run_name="corner_plot-{name}",
)
async def model_corner_plot(
    name: str,
    filename: str,
    flat_samples: np.ndarray,
    labels: List[str] | None = None,
    truths: np.ndarray | None = None,
) -> None:
    """Produce a corner plot

    Args:
    name (str) : model name
    filename (str) : base file name to which to save figure
    flat_samples (np.ndarray): the flatten samples
    labels (List[str]) : parameter labels
    quantiles (List[float]) : quantile list with default being [16, 50, 84]

    """
    fig = corner.corner(
        flat_samples,
        labels=labels,
        truths=truths,
        title=name,
    )
    fig.savefig(f"{filename}_corner.png")
    # save the figure so that it can be directly viewed from the Prefect UI
    await upload_image_as_artifact(Path(f"{filename}_corner.png"))


@task(
    retries=5,
    retry_delay_seconds=2,
    timeout_seconds=600,
    task_run_name="plot_fit-{model.name}",
)
async def model_plot_fit(
    model: LikelihoodModel,
    filename: str,
    flat_samples: np.ndarray,
    sample_size: int = 100,
    cdata: str = "black",
    alpha: float = 0.1,
    c1: str = "orange",
) -> None:
    """Plot model fits to data

    Args:
        model (LikelihoodModel) : model
        filename (str) : base file name to which to save figure
        flat_samples (np.ndarray): the flatten samples
        sample_size (int) : number of samples to plot

    """

    inds = np.random.randint(len(flat_samples), size=sample_size)
    if model.data_ndim > 1:
        fig, axes = plt.subplot(1, model.data_ndim)
        for i in range(model.data_ndim):
            plt.errorbar(
                model.X[:, i],
                model.ref,
                xerr=model.xerr,
                yerr=model.yerr,
                color=cdata,
                zorder=10,
                ax=axes[i],
            )
            for ind in inds:
                sample = flat_samples[ind]
                plt.plot(
                    model.X[:, i],
                    model.model_func(model.X, sample),
                    alpha=alpha,
                    color=c1,
                    zorder=1,
                    ax=axes[i],
                )
        fig.savefig(f"{filename}_modelcomp.png")
    else:
        fig = plt.figure()
        plt.errorbar(
            model.X, model.ref, xerr=model.xerr, yerr=model.yerr, color=cdata, zorder=10
        )
        for ind in inds:
            sample = flat_samples[ind]
            plt.plot(
                model.X,
                model.model_func(model.X, sample),
                alpha=alpha,
                color=c1,
                zorder=1,
            )
        fig.savefig(f"{filename}_modelcomp.png")

    # save the figure so that it can be directly viewed from the Prefect UI
    await upload_image_as_artifact(Path(f"{filename}_modelcomp.png"))


async def model_analysis_wrapper(
    myflow: HybridQuantumWorkflowBase,
    model: LikelihoodModel,
    sampler: emcee.EnsembleSampler,
    quantiles: List[np.float64] | None = None,
    fit_filename: str | None = None,
    chain_filename: str | None = None,
    plot_dir: str = "./",
    compare_plot: bool = True,
) -> Tuple[float, np.ndarray]:
    future = model_flatten_sampler.submit(sampler=sampler)
    flat_samples = future.result()
    ndim = flat_samples.shape[1]
    future = model_estimate_evidence.submit(name=model.name, sampler=sampler)
    evidence = future.result()
    future = await model_report_fit.submit(
        name=model.name,
        sampler=sampler,
        labels=model.param_labels,
        quantiles=quantiles,
        evidence=evidence,
        fit_filename=fit_filename,
    )
    fit_values = await future.result()
    if chain_filename is not None:
        future = await model_save_chain(
            model=model,
            filename=chain_filename,
            flat_samples=flat_samples,
            evidence=evidence,
            quantiles=quantiles,
            fit_values=fit_values,
        )
    future = await model_corner_plot.submit(
        name=model.name, flat_samples=flat_samples, filename=f"{plot_dir}/{model.name}"
    )
    await future.results()
    if compare_plot:
        # need to setup min and max values of the data space
        future = await model_plot_fit(
            model=model, filename=f"{plot_dir}/{model.name}", flat_samples=flat_samples
        )
        await future.results()
    return evidence, fit_values


@flow(
    name="Analyse Model Fit flow",
    flow_run_name="model_analysis-{date:%Y-%m-%d:%H:%M:%S}",
    description="Running tasks that gets quantiles, plots results, gets the Bayes evidence ",
    retries=3,
    retry_delay_seconds=10,
    log_prints=True,
)
async def model_analysis_workflow(
    myflow: HybridQuantumWorkflowBase,
    model: LikelihoodModel,
    sampler: emcee.EnsembleSampler,
    compare_plot: bool = False,
    quantiles: List[np.float64] | None = None,
    fit_filename: str | None = None,
    chain_filename: str | None = None,
    date: datetime.datetime = datetime.datetime.now(),
):
    """Flow for analysing a model fit (assumes analysis is not as computationally heavy as fit)

    Args:
        myflow (HybridQuantumWorkflowBase) : workflow structure
        model (LikelihoodModel) : model
        sampler (emcee.EnsembleSampler) :
    """
    # it might be possible to call a flow with .fn()
    await model_analysis_wrapper(
        myflow=myflow,
        model=model,
        sampler=sampler,
        compare_plot=compare_plot,
        quantiles=quantiles,
        fit_filename=fit_filename,
        chain_filename=chain_filename,
    )


@flow(
    name="Model Fitting flow",
    flow_run_name="model_fitting-{date:%Y-%m-%d:%H:%M:%S}",
    description="Running tasks that get the Bayes evidence and optimal parameters of a model",
    retries=3,
    retry_delay_seconds=10,
    log_prints=True,
)
async def model_fit_and_analyse_workflow(
    myflow: HybridQuantumWorkflowBase,
    model: LikelihoodModel,
    analysis_dask_runner: str | None = None,
    compare_plot: bool = False,
    quantiles: List[np.float64] | None = None,
    fit_filename: str | None = None,
    chain_filename: str | None = None,
    date: datetime.datetime = datetime.datetime.now(),
):
    """Flow for fitting a model.

    Args:
        myflow (HybridQuantumWorkflowBase) : workflow structure
        model (LikelihoodModel) : model
        analysis_dask_runner (str) : if provided, launch new flow to do analysis, otherwise, use same dask runner
    """
    logger = get_run_logger()
    logger.info(f"Running sampler")
    future = model_run_sampler.submit(
        model=model,
        log_prob=log_probability,
    )
    logger.info(f"Flattening and analysing results ...")
    fname = model.name + "_fit"
    sampler = future.result()
    if analysis_dask_runner is None:
        await model_analysis_wrapper(
            myflow=myflow,
            model=model,
            sampler=sampler,
            compare_plot=compare_plot,
            quantiles=quantiles,
            fit_filename=fit_filename,
            chain_filename=chain_filename,
        )
    else:
        analysis_flow = model_analysis_workflow.with_options(
            task_runner=myflow.gettaskrunner(analysis_dask_runner)
        )
        asyncio.run(
            analysis_flow(
                myflow=myflow,
                model=model,
                sampler=sampler,
                compare_plot=compare_plot,
                quantiles=quantiles,
                fit_filename=fit_filename,
                chain_filename=chain_filename,
            )
        )


@flow(
    name="Example Multi-Model Estimation flow",
    flow_run_name="model_estimation-{date:%Y-%m-%d:%H:%M:%S}",
    description="Running CPU/GPU flows that get the bayes evidence and optimal model",
    retries=3,
    retry_delay_seconds=10,
    log_prints=True,
)
async def workflow(
    myflow: HybridQuantumWorkflowBase,
    model_info: Dict[str, LikelihoodModel],
) -> None:
    """Multi-model workflow

    Args:
        model_info (Dict): A dictionary with model name indicating filename, whether to read the data, compute resources, labels of
    """
    logger = get_run_logger()

    model_flows = dict()
    for k in model_info.keys():
        m = model_info[k]
        model_flows[k] = model_fit_and_analyse_workflow.with_options(
            task_runner=myflow.gettaskrunner(m.dask_runner)
        )

    tasks = dict()
    async with asyncio.TaskGroup() as tg:
        for k in model_info.keys():
            tasks[k].append(
                tg.create_task(
                    model_flows[k](
                        myflow=myflow,
                        model=model_info[k],
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
    model_info["Linear"] = LikelihoodModel(
        name="Linear",
        dask_runner="CPU",
        model_func=linear_model,
        filename="simple_data.h5",
        data_keys=("x", "y"),
        param_labels=["m", "b"],
    )
    model_info["Parabolic"] = LikelihoodModel(
        name="Parabolic",
        dask_runner="GPU",
        model_func=parabolic_model,
        filename="simple_data.h5",
        data_keys=("x", "y"),
        param_labels=["a", "b", "c"],
    )
    model_info["Hyperfit"] = LikelihoodModel(
        name="Hyperfit",
        dask_runner="CPU",
        model_func=hyperfit,
        filename="simple_data.h5",
        data_keys=("x", "y"),
        param_labels=[r"$\alph$", r"$\beta$", r"$\sigma_y$"],
    )

    asyncio.run(workflow(myflow=myflow, model_info=model_info))


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
