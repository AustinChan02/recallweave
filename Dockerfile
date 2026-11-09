FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY migrations ./migrations
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install --no-cache-dir .
EXPOSE 8100
CMD ["uvicorn", "recallweave.api.app:app", "--host", "0.0.0.0", "--port", "8100"]
