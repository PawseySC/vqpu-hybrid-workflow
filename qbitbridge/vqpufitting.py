"""
@file vqpufitting.py
@brief Collection of tasks related to model fitting.

"""

import sys, os, re
import ast
from pathlib import Path
from time import sleep
import datetime
from typing import List, Set, Callable, Tuple, Dict, Any
import warnings

# import qbitbridge
from .vqpubase import HybridQuantumWorkflowBase
from .utils import (
    EventFile,
    check_python_installation,
    save_artifact,
    upload_image_as_artifact,
    check_file_can_be_created,
)
from prefect.artifacts import Artifact
import asyncio
from prefect import task, flow
from prefect_dask import DaskTaskRunner
from prefect.logging import get_run_logger
import numpy as np
import matplotlib.pyplot as plt

libcheck = check_python_installation("emcee")
if not libcheck:
    raise ImportError("Missing emcee library, cannot run fitting tasks")
import emcee
import corner

libcheck = check_python_installation("h5py")
if not libcheck:
    raise ImportError("Missing h5py library, cannot save data using h5py")
import h5py


class LikelihoodModel:
    """model object that summarise properties of the model and how to evalate it"""

    def __init__(
        self,
        name: str,
        dask_runner: str,
        model_func: Callable,
        model_dim: int,
        log_prob: Callable,
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
        self.model_dim = model_dim
        """Number of dimensions to model"""
        self.log_prob = log_prob
        """Log probability Function to call"""
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
        self.Xlims = np.zeros([self.data_ndim, 2])
        if self.data_ndim > 1:
            for i in range(self.data_ndim):
                self.Xlims[i][:] = np.array(
                    [np.min(self.X[:, i]), np.max(self.X[:, i])]
                )
        else:
            self.Xlims = np.array([np.min(self.X), np.max(self.X)])
        if self.param_labels is None:
            self.param_labels = [f"param-{i}" for i in range(self.model_dim)]
        else:
            if len(self.param_labels) < self.model_dim:
                warnings.warn(
                    "labels smaller than model dimensions, adding labels",
                    RuntimeWarning,
                )
                cursize: int = len(self.param_labels)
                self.param_labels += [
                    f"param-{i}" for i in range(cursize, self.model_dim)
                ]
            elif len(self.param_labels) > self.model_dim:
                warnings.warn(
                    "label mismatch, have more labels than model parameters. Ignoring other labels",
                    RuntimeWarning,
                )
                self.param_labels = [
                    self.param_labels[i] for i in range(self.model_dim)
                ]

    def load_data(self):
        """
        Load data from HDF5, may want to alter this to MPI enabled h5py load

        Returns:
            inputs and output return
        """
        with h5py.File(self.filename, "r") as f:
            self.X = np.array(f[self.data_keys[0]])
            self.ref = np.array(f[self.data_keys[1]])


class LikelihoodFit:
    def __init__(
        self,
        name: str,
        evidence: np.float64,
        quantiles: List[float],
        params: List[Tuple[str, List[np.float64]]],
    ) -> None:
        self.name = name
        """Model name of fit"""
        self.evidence = evidence
        """bayes evidence"""
        self.quantiles = quantiles
        """quantiles of the parameters"""
        self.params = params
        """parameters"""
        self.ndim = len(params)
        """number of parameters"""

    def __str__(self) -> str:

        form = "{0} = {1:.3f}_{{-{2:.3f}}}^{{+{3:.3f}}} = [{4:.3f}, {5:.3f}, {6:.3f}]"

        info: str = f"# Model fit for {self.name}\n"
        info += f"Evidence = {self.evidence}\n"
        info += f"NParams = {self.ndim}\n"
        info += f"Quantiles = {self.quantiles}\n"
        for i in range(self.ndim):
            label = self.params[i][0]
            mcmc = self.params[i][1]
            q = np.diff(mcmc)
            txt = form.format(label, mcmc[1], q[0], q[1], mcmc[0], mcmc[1], mcmc[2])
            info += txt + "\n"
        return info

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
        if "# Model fit for " not in arg:
            raise ValueError("Not a Model Fit string")

        lines = arg.strip().split("\n")[1:][:-2]
        # print(lines)
        name = lines[0].split("# Model fit for ")[0]
        evidence = np.float64(lines[1].split("Evidence = ")[1])
        nparam = int(lines[2].split("NParams = ")[1])
        quantiles = ast.literal_eval(lines[3].split("Quantiles = ")[1])
        params = []
        for l in lines[4 : nparam + 4]:
            label = l.split(" = ")[0]
            values = ast.literal_eval(l.split(" = ")[2])
            values = [np.float64(v) for v in values]
            params.append((label, values))
        # need to also parse the noise, calibration, connectivity data so that
        # they are not strings
        return cls(name=name, quantiles=quantiles, evidence=evidence, params=params)


class LikelihoodModelRuntime:
    def __init__(
        self,
        outputdir: str = "./",
        nwalkers: int = 32,
        nsteps: int = 5000,
        nburnin: np.int64 = np.int64(100),
        init_pos: np.ndarray | None = None,
        analysis_dask_runner: str | None = None,
        compare_plot: bool = True,
        quantiles: List[float] | None = None,
        fit_filename: str | None = None,
        chain_filename: str | None = None,
        show_progress: bool = False,
    ) -> None:
        self.outputdir = outputdir
        """output directory"""
        self.nwalkers = nwalkers
        """number of walkers"""
        self.nsteps = nsteps
        """number of steps to take"""
        self.nburnin = nburnin
        """number to burn in before analysing"""
        self.init_pos = init_pos
        """initial position of walkers in model space"""
        self.analysis_dask_runner = analysis_dask_runner
        """what dask runner to use when analysing data"""
        self.quantiles = quantiles
        """quantiles when analysing model fit"""
        self.compare_plot = compare_plot
        """filename of comparison plot"""
        self.fit_filename = fit_filename
        """file name of where to save data"""
        self.chain_filename = chain_filename
        """file name of where to save chain"""
        self.show_progress = show_progress
        """whether to have emcee report progress"""


@task(
    retries=5,
    retry_delay_seconds=2,
    timeout_seconds=600,
    task_run_name="run_sampler-{model.name}",
)
def model_run_sampler(
    model: LikelihoodModel,
    nwalkers: int = 32,
    nsteps: int = 5000,
    init_pos: np.ndarray | None = None,
    show_progress: bool = False,
) -> emcee.EnsembleSampler:
    """Run emcee sampler

    Args:
        model (LikelihoodModel) : the model class that contains relevant info
        nwalkers (int) : number of samplers
        nsteps (int) : number of steps to take
        init_pos (np.ndarray) : initial position of the walkers
        show_progress (bool) : show the progress of the sampler

    Returns:
        EnsembleSampler that has sampled the model
    """
    # initialize walkers
    if init_pos is None:
        init_pos = np.random.randn(nwalkers, model.model_dim)
    else:
        nwalkers = init_pos.shape[0]
    # setup the sampler
    sampler = emcee.EnsembleSampler(
        nwalkers,
        model.model_dim,
        model.log_prob,
        args=(model.model_func, model.X, model.ref),
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
def model_estimate_evidence(name: str, sampler: emcee.EnsembleSampler) -> np.float64:
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
    log_evidence = np.float64(np.logaddexp.reduce(log_probs) - np.log(len(log_probs)))
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
    nthin: np.int32 | None = None,
    nburnin: np.int64 | None = None,
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
        nthin = np.int32(np.max(tau) / 2)
    if nburnin is None:
        nburnin = np.int64(100)
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
    evidence: np.float64,
    labels: List[str],
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
    if quantiles is None:
        quantiles = [16.0, 50.0, 84.0]
    else:
        quantiles = np.sort(quantiles)
    results = np.zeros((ndim, len(quantiles)))
    params = []
    for i in range(ndim):
        # will need to generalize this eventually
        mcmc = np.percentile(flat_samples[:, i], quantiles)
        results[i][:] = mcmc[:]
        params.append((labels[i], mcmc))
    modelfit = LikelihoodFit(
        name=name, evidence=evidence, quantiles=quantiles, params=params
    )
    info = modelfit.__str__()
    logger.info(info)
    if fit_filename is not None:
        if not check_file_can_be_created(fit_filename):
            message: str = f"Cannot create file {fit_filename}"
            raise ValueError(message)
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
    task_run_name="report_fit-{name}",
)
async def model_retrieve_fit(
    name: str,
) -> LikelihoodFit:
    """Retrieve model parameters

    Args:
        name (str) : model name

    Returns:
        LikelihoodFit of the model
    """
    # strip name should be all lower case with no special characters
    stripname = name.lower().strip("-").strip("_").strip(" ")
    artifact = await Artifact.get(key=stripname)
    if artifact is None:
        raise ValueError(
            "asking workflow to get active qpu data but no active qpu found!"
        )
    data = dict(artifact)["data"]
    return LikelihoodFit.from_string(data)


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
    labels: List[str],
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
    msize: float = 2.0,
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
                marker="o",
                markersize=msize,
                linestyle="None",
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
            model.X,
            model.ref,
            xerr=model.xerr,
            yerr=model.yerr,
            color=cdata,
            marker="o",
            markersize=msize,
            linestyle="None",
            zorder=10,
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
    outputdir: str,
    nburnin: np.int64 | None = None,
    quantiles: List[np.float64] | None = None,
    fit_filename: str | None = None,
    chain_filename: str | None = None,
    compare_plot: bool = True,
) -> Tuple[float, np.ndarray]:
    # get stats from the sampler
    future = model_flatten_sampler.submit(sampler=sampler, nburnin=nburnin)
    flat_samples = future.result()
    future = model_estimate_evidence.submit(name=model.name, sampler=sampler)
    evidence = future.result()

    # make fit dir and update file names if necessary
    fitdir = f"{outputdir}/fits/"
    os.makedirs(fitdir, exist_ok=True)
    if fit_filename is not None:
        fit_filename = f"{fitdir}/{fit_filename}"
    if chain_filename is not None:
        chain_filename = f"{fitdir}/{chain_filename}"

    future = await model_report_fit.submit(
        name=model.name,
        flat_samples=flat_samples,
        labels=model.param_labels,
        quantiles=quantiles,
        evidence=evidence,
        fit_filename=fit_filename,
    )
    fit_values = await future.result()

    # make plot directory
    plotdir = f"{outputdir}/plots/"
    os.makedirs(plotdir, exist_ok=True)

    async with asyncio.TaskGroup() as tg:
        if chain_filename is not None:
            tg.create_task(
                model_save_chain(
                    model=model,
                    filename=chain_filename,
                    flat_samples=flat_samples,
                    evidence=evidence,
                    quantiles=quantiles,
                    fit_values=fit_values,
                )
            )
        tg.create_task(
            model_corner_plot.submit(
                name=model.name,
                flat_samples=flat_samples,
                filename=f"{plotdir}/{model.name}",
                labels=model.param_labels,
            )
        )
        if compare_plot:
            # need to setup min and max values of the data space
            tg.create_task(
                model_plot_fit(
                    model=model,
                    filename=f"{plotdir}/{model.name}",
                    flat_samples=flat_samples,
                )
            )
    return evidence, fit_values


@flow(
    name="Analyse Model Fit flow",
    flow_run_name="model_analysis-{model.name}-{date:%Y-%m-%d:%H:%M:%S}",
    description="Running tasks that gets quantiles, plots results, gets the Bayes evidence ",
    retries=3,
    retry_delay_seconds=10,
    log_prints=True,
)
async def model_analysis_workflow(
    myflow: HybridQuantumWorkflowBase,
    model: LikelihoodModel,
    sampler: emcee.EnsembleSampler,
    outputdir: str,
    nburnin: np.int64 | None = None,
    compare_plot: bool = True,
    quantiles: List[np.float64] | None = None,
    fit_filename: str | None = None,
    chain_filename: str | None = None,
    date: datetime.datetime = datetime.datetime.now(),
):
    """Flow for analysing a model fit (assumes analysis is not as computationally heavy as fit)

    Args:
        myflow (HybridQuantumWorkflowBase) : workflow structure
        model (LikelihoodModel) : model
        sampler (emcee.EnsembleSampler) : sampler to analyse
        nburnin (np.int64) : burn-in number
        compare_plot (bool) : Whether to do comparison plot
        quantiles (List[np.float64]) : provide quantiles when calculating model fit
        fit_filename (str) : optional filename to save fit
        chain_filename (str) : optional filename to save chain and fit

    """
    # it might be possible to call a flow with .fn()
    await model_analysis_wrapper(
        myflow=myflow,
        model=model,
        sampler=sampler,
        outputdir=outputdir,
        nburnin=nburnin,
        compare_plot=compare_plot,
        quantiles=quantiles,
        fit_filename=fit_filename,
        chain_filename=chain_filename,
    )


@flow(
    name="Model Fitting flow",
    flow_run_name="model_fitting-{model.name}-{date:%Y-%m-%d:%H:%M:%S}",
    description="Running tasks that get the Bayes evidence and optimal parameters of a model",
    retries=3,
    retry_delay_seconds=10,
    log_prints=True,
)
async def model_fit_and_analyse_workflow(
    myflow: HybridQuantumWorkflowBase,
    model: LikelihoodModel,
    nwalkers: int = 32,
    nsteps: int = 5000,
    outputdir: str = "./",
    nburnin: np.int64 | None = None,
    init_pos: np.ndarray | None = None,
    show_progress: bool = False,
    analysis_dask_runner: str | None = None,
    compare_plot: bool = True,
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
        nwalkers=nwalkers,
        nsteps=nsteps,
        init_pos=init_pos,
        show_progress=show_progress,
    )
    logger.info(f"Flattening and analysing results ...")
    fname = model.name + "_fit"
    sampler = future.result()
    if analysis_dask_runner is None:
        await model_analysis_wrapper(
            myflow=myflow,
            model=model,
            sampler=sampler,
            outputdir=outputdir,
            nburnin=nburnin,
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
                outputdir=outputdir,
                nburnin=nburnin,
                compare_plot=compare_plot,
                quantiles=quantiles,
                fit_filename=fit_filename,
                chain_filename=chain_filename,
            )
        )


@flow(
    name="Multi-Model Estimation flow",
    flow_run_name="multi_model_fitting-{date:%Y-%m-%d:%H:%M:%S}",
    description="Running flows that get the bayes evidence and optimal model",
    retries=3,
    retry_delay_seconds=10,
    log_prints=True,
)
async def multi_model_flow(
    myflow: HybridQuantumWorkflowBase,
    model_info: Dict[str, LikelihoodModel],
    model_run_args: Dict[str, LikelihoodModelRuntime] | None = None,
    date: datetime.datetime = datetime.datetime.now(),
) -> None:
    """Multi-model workflow

    Args:
        model_info (Dict): A dictionary with model name indicating filename, whether to read the data, compute resources, labels of
    """
    logger = get_run_logger()

    model_flows = dict()
    if model_run_args is None:
        model_run_args = dict()
    for k in model_info.keys():
        m = model_info[k]
        model_flows[k] = model_fit_and_analyse_workflow.with_options(
            task_runner=myflow.gettaskrunner(m.dask_runner)
        )
        if k not in model_run_args.keys():
            warnings.warn(
                f"model {k} missing explicit runtime args, running with defaults",
                RuntimeWarning,
            )
            model_run_args[k] = LikelihoodModelRuntime()

    tasks = dict()
    async with asyncio.TaskGroup() as tg:
        for k in model_info.keys():
            tasks[k] = tg.create_task(
                model_flows[k](
                    myflow=myflow,
                    model=model_info[k],
                    nwalkers=model_run_args[k].nwalkers,
                    nsteps=model_run_args[k].nsteps,
                    nburnin=model_run_args[k].nburnin,
                    init_pos=model_run_args[k].init_pos,
                    analysis_dask_runner=model_run_args[k].analysis_dask_runner,
                    compare_plot=model_run_args[k].compare_plot,
                    quantiles=model_run_args[k].quantiles,
                    fit_filename=model_run_args[k].fit_filename,
                    chain_filename=model_run_args[k].chain_filename,
                    show_progress=model_run_args[k].show_progress,
                )
            )
    logger.info("Finished running models")
    logger.info("Retriving models ... ")
    futures = dict()
    for k in model_info.keys():
        futures[k] = await model_retrieve_fit.submit(name=model_info[k].name)
    fits = dict()
    for k in model_info.keys():
        fits[k] = await futures[k].result()
    for k in model_info.keys():
        logger.info(fits[k])
