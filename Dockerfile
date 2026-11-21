FROM python:3.11.11-slim
WORKDIR /app
COPY . .
RUN python -m pip install --disable-pip-version-check --no-cache-dir --no-index --find-links=runtime/wheels -r requirements.txt -r requirements-judge.txt
RUN mkdir -p /opt/proofledger/wasmtime && python -m proofledger.upload_runtime --precompile
CMD ["python", "-m", "proofledger"]
