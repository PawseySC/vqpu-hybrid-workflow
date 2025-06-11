#!/bin/bash -l
#start_postgres.sh

help="This script will configure the prefect environment, and if requested start the
postgres server necessary.

Options:
    -p <> - provide the password
    -a <> - provide the address
    -s <> - provide the scratch space
    -c <> - singularity container image 
    -v  - verbose
    -S  - will attempt to start the postgres server from a singularity container
    -h  - will print this help page
    

Usage:
start_postgres.sh [-p <> | -a <> | -s <> | -v | -S | -h]
"

START_POSTGRES=0
VERBOSE=0

while getopts 'p:a:s:c:Svh' arg; do
    case $arg in
    p)
        echo "Setting the Postgres password: $OPTARG"
        postgres_pass=$OPTARG
        ;;
    a)
        echo "Setting the Postgres address: $OPTARG"
        postgres_addr=$OPTARG
        ;;
    s)
        echo "Setting the Postgres scratch space: $OPTARG"
        postgres_scratch=$OPTARG
        ;;
    c)
        echo "Container image: $OPTARG"
        container_image=$OPTARG
        ;;
    S)
        START_POSTGRES=1
        ;;
    v)
        VERBOSE=1
        ;;
    *)
        echo "$help"
        exit 1
        ;;
    esac
done

# Now set up some postgres values
# might want a more secure way of setting the password. 
# might not want it in the script at all. 
if [[ -z $postgres_pass ]]; then 
    echo "No password provided"
    exit 1
fi
if [[ -z $postgres_addr ]]; then 
    echo "No address provided"
    exit 1
fi
if [[ -z $postgres_scratch ]]; then 
    echo "No scratch space provided"
    exit 1
fi


export POSTGRES_PASS=$postgres_pass
export POSTGRES_ADDR=$postgres_addr
export POSTGRES_USER='postgres'
export POSTGRES_DB=orion
export POSTGRES_SCRATCH=$postgres_scratch

if [[ $VERBOSE -eq 1 ]]; then 
    echo "Postgres environment"
    env | grep "POSTGRES"
fi 

if [[ $START_POSTGRES -eq 1 ]]
then
    echo "Starting postgres ... "
    # Need singulaity to bind the postgres scratch area
    SINGULARITY_BINDPATH+=${SINGULARITY_BINDPATH}:"$POSTGRES_SCRATCH"
    export SINGULARITY_BINDPATH


    if [[ ! -e "${POSTGRES_SCRATCH}/pgdata" ]]; then
        echo "Creating pgdata for the postgres server operation"
        mkdir -p pgdata
    fi

    if [[ -z ${container_image} ]]
    then 
        if [[ ! -e postgres_latest.sif ]]
        then
            echo "Downloading the latest postgres docker container"
            singularity pull docker://postgres            
        fi
        container_image=$(pwd)/postgres_latest.sif
    fi 
    # set the singularity arguments 
    singargs="--cleanenv --bind $POSTGRES_SCRATCH:/var"
    singargs="--bind $POSTGRES_SCRATCH:/var"

    # define the appropriate environment variables
    SINGULARITYENV_POSTGRES_PASSWORD=$POSTGRES_PASS SINGULARITYENV_POSTGRES_DB=$POSTGRES_DB  SINGULARITYENV_PGDATA=$POSTGRES_SCRATCH/pgdata \
    singularity run ${singargs} ${container_image} -c max_connections=1000 -c shared_buffers=1024MB
fi
