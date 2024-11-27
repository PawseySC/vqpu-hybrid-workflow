from prefect import flow, task, get_run_logger
from prefect.artifacts import create_markdown_artifact, Artifact

@task
def save_data(data):
    create_markdown_artifact(
        key = 'key',
        markdown = data,
        description = "Data to be shared between subflows"
    )

@flow
def subflow_1():
    data = {"key": "value"}
    save_data(data['key'])

@flow
def subflow_2():
    logger = get_run_logger()
    artifact = Artifact.get(key="key")
    logger.info(f"Retrieved data: {artifact.data}")

@flow
def parent_flow():
    subflow_1()
    subflow_2()

# Run the flow
if __name__ == "__main__":
    parent_flow()
