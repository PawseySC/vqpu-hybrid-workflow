#!/bin/bash 
SCRIPT_DIR="$(dirname "$(realpath "$0")")"
. ${SCRIPT_DIR}/aws_profile_env.sh 
echo "Logging into AWS ${AWS_PROFILE} "
aws sso login --profile ${AWS_PROFILE}  --use-device-code

