"""
Some utility functions around the creation of Prefect task runners
For this work we will be using Dask backed workers to perform the compute
operations.
"""

import os
from glob import glob
from pathlib import Path
from typing import Any, Dict, List, Tuple
import copy

import yaml
from prefect_dask import DaskTaskRunner
from dask_jobqueue import SLURMCluster
from dask_jobqueue import PBSCluster

# from dask_kubernetes.classic import KubeCluster
from .utils import probe_cluster_scheduler, SchedulerInfo

_SUPPORTED_CLUSTER_TYPES: frozenset[str] = frozenset({"SLURMCluster", "PBSCluster"})


def list_packaged_clusters(yaml_files_dir: str = "./") -> List[str]:
    """
    Return a list of cluster names that are available in the packaged set of
    dask_jobqueue specification YAML files.

    Args:
        yaml_files_dir (str): string for directory to search for clusters

    Returns:
        list (str): A list of preinstalled dask_jobqueue cluster specification files
    """

    yaml_files = glob(f"{yaml_files_dir}/*yaml")
    clusters = [Path(f).stem for f in yaml_files]

    return clusters


def get_cluster_spec(cluster: str | Path, clustype: str = "slurm") -> Dict[Any, Any]:
    """Given a cluster name, obtain the appropriate SLURM or PBS configuration
    file appropriate for use with SLURMCluster or PBSCluster.

    This cluster spec is expected to be consistent with the cluster_class
    and cluster_kwargs parameters that are used by dask_jobqueue based
    specifications.

    Args:
        cluster (str|Path): Name of cluster or path to a configuration to look up for processing
        clustype (str): cluster type, must be _SUPPORTED_CLUSTER_TYPES

    Raises:
        ValueError: Raised when cluster is not in KNOWN_CLUSTERS and has not corresponding YAML file.

    Returns:
        dict[Any, Any]: Dictionary of know options/parameters for dask_jobqueue.SLURMCluster
    """

    yaml_file = None
    import glob

    _KNOWN_CLUSTERS = glob.glob(
        f"{os.path.dirname(os.path.abspath(__file__))}/../workflow/clusters/*.yaml"
    )

    if Path(cluster).exists():
        yaml_file = cluster
    else:
        yaml_file = f"{os.path.dirname(os.path.abspath(__file__))}/../workflow/clusters/{cluster}.yaml"

    if yaml_file is None or not Path(yaml_file).exists():
        raise ValueError(
            f"{cluster} is not known, or its YAML file could not be loaded."
        )

    with open(yaml_file, "r") as in_file:
        spec = yaml.load(in_file, Loader=yaml.Loader)

    return spec


def get_dask_runners(
    cluster: str = "ella",
    extra_cluster_kwargs: Dict[str, Any] | None = None,
) -> Dict[str, DaskTaskRunner | Dict[str, str]]:
    """Creates and returns a DaskTaskRunner configured to established a SLURMCluster instance
    to manage a set of dask-workers.

    Args:
        cluster (str): The cluster name that will be used to search for a cluster specification file.
        This could be the name of a known cluster, or the name of a yaml file installed
        among the `cluster_configs` directory of the aces module.
        extra_cluster_kwargs (Dict): Optional arguments to be passed to update the dask task runners of a cluster

    Returns:
        Dict[str, DaskTaskRunner | Dict[str, str]]: dictionary of dask task runners with specific names,
        the associated job scripts and the specs need to create a new dask task runner
    """

    specs = get_cluster_spec(cluster)
    task_runners = {"jobscript": dict(), "specs": dict()}
    for specname in specs.keys():
        # still need to figure out how to encorporated distributed options
        if specname == "distributed":
            continue
        cluster_config = specs[specname]
        if extra_cluster_kwargs is not None:
            cluster_config["cluster_kwargs"].update(extra_cluster_kwargs)
        task_runners[specname] = DaskTaskRunner(**cluster_config)
        # load the appropriate job script
        cc = str(cluster_config["cluster_class"]).replace("dask_jobqueue.", "")
        if cc not in _SUPPORTED_CLUSTER_TYPES:
            raise ValueError(
                f"{cluster} contains spec for an unknown/unsupported cluster class {cc}. Supported types dask_jobqueue.{_SUPPORTED_CLUSTER_TYPES}"
            )
        sched: SchedulerInfo = probe_cluster_scheduler()
        if cc != sched.scheduler:
            raise ValueError(
                f"Loaded cluster class {cc} does not match the scheduler {sched.scheduler} for cluster {cluster}!"
            )
        if "SLURMCluster" in cc:
            task_runners["jobscript"][specname] = SLURMCluster(
                **cluster_config["cluster_kwargs"]
            ).job_script()
        elif "PBSCluster" in cc:
            task_runners["jobscript"][specname] = PBSCluster(
                **cluster_config["cluster_kwargs"]
            ).job_script()
        # elif "KubeCluster" in cc:
        #     task_runners["jobscript"][specname] = KubeCluster(
        #         **cluster_config["cluster_kwargs"]
        #     ).job_script()

        task_runners["specs"][specname] = copy.deepcopy(cluster_config)
        task_runners["jobscript"][specname] = (
            f"Cluster class:{cc}\n-- Script --\n"
            + task_runners["jobscript"][specname]
            + "\n-- End Script --\n"
        )
    return task_runners
