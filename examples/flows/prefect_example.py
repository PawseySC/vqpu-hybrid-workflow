from prefect import flow, task
import numpy as np

@task(retries=1, log_prints=True)
def extract_data():
    data = np.zeros(100)
    return data

@task
def transform_data(data):
    # process the data
    processed = data + np.ones(100)
    return processed

@task
def load_data(data):
    # load data to database
    #load_into_db(data)
    print(data)

@flow(name="etl_pipeline")
def etl_pipeline_flow():
    raw = extract_data()
    processed = transform_data(raw)
    load_data(processed)

if __name__ == "__main__":
    # For local testing
    etl_pipeline_flow()
