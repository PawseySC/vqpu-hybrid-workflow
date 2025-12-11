#!/bin/bash -l
#start_prefect.sh

help="This script will configure the prefect environment, and if requested start the
postgres server necessary.

Options:
    -e <> - (optional) python venv to source
    -H <> - prefect home
    -p <> - provide the postgres password
    -a <> - provide the postgres address
    -s <> - provide the postgres scratch space
    -v  - verbose
    -S  - will attempt to start the postgres server from a singularity container
    -h  - will print this help page

Usage:
start_prefect.sh [-p <> | -a <> | -s <> | -e <> | -H <> | -v | -S | -h]
"

START_SERVER=0
VERBOSE=0

while getopts 'p:a:s:e:H:Svh' arg; do
    case $arg in
    e)
        echo "Prefect Python Environment to source before running prefect: $OPTARG"
        prefect_python_venv=$OPTARG
        ;;
    H)
        echo "Prefect home: $OPTARG"
        prefect_home=$OPTARG
        ;;
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
    S)
        echo "Will attempt to start orion server"
        START_SERVER=1
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
if [[ -z $prefect_home ]]; then 
    echo "No prefect home dir provided"
    exit 1
fi
if [[ -z $prefect_python_venv ]]; then 
    echo "No python virtual env provided"
    exit 1
fi

export POSTGRES_PASS=$postgres_pass
export POSTGRES_ADDR=$postgres_addr
export POSTGRES_USER='postgres'
export POSTGRES_DB=orion
export POSTGRES_SCRATCH=$postgres_scratch

export PREFECT_API_URL="http://${POSTGRES_ADDR}:4200/api"
export PREFECT_SERVER_API_HOST="127.0.0.1"

export PREFECT_API_DATABASE_CONNECTION_URL="postgresql+asyncpg://$POSTGRES_USER:$POSTGRES_PASS@$POSTGRES_ADDR:5432/$POSTGRES_DB"

# This establishes a larger number of workers for prefect on the webserver (uvicorn under the hood)
export WEB_CONCURRENCY=16
# These can be tweaked to allow for more persistent data connections
export PREFECT_SQLALCHEMY_POOL_SIZE=5
export PREFECT_SQLALCHEMY_MAX_OVERFLOW=10
#PREFECT_HOME="$(pwd)/prefect"
export PREFECT_HOME=$prefect_home

if [[ $VERBOSE -eq 1 ]]; then 
    echo "Prefect environment"
    env | grep "PREFECT"
    env | grep "POSTGRES"
fi 


if [[ $START_SERVER -eq 1 ]]
then
    if [[ ! -z $prefect_python_venv ]]; then 
        echo "Loading python venv using ${prefect_python_venv}"
        source ${prefect_python_venv}/bin/activate
    fi
    # before could just run prefect but here are issues with long living processes and 
    # memory leaks. Instead explicitly invoke uvicorn to control this 
    # prefect server start --host 0.0.0.0
    # when using uvicorn, it might be necessary to set --app-dir explicitly 
    # but I think if the PYTHONPATHs are correct, then nothing is required. 
    # calling uvicorn with default port used by prefect
    python -m uvicorn \
        --app-dir ${prefect_python_venv}/lib/python3.11/site-packages/ \
        --factory prefect.server.api.server:create_app \
        --host 0.0.0.0 \
        --port 4200 \
        --timeout-keep-alive 10 \
        --limit-max-requests 4096 \
        --timeout-graceful-shutdown 7200 & 
    echo "Set the following"
    echo "export PREFECT_API_URL=http://${POSTGRES_ADDR}:4200/api"
    # and then also set the server 
fi


