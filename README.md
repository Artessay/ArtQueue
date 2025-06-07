# ArtQueue

```bash
# Create a new environment
conda create -n queue python=3.13 -y
conda activate queue
```

```bash
# Install dependencies
pip install -e .
```

```bash
# set environment variables
export MAX_CONCURRENCY=5  # max concurrency
export OPENAI_API_KEY=your_openai_api_key
export OPENAI_API_BASE=https://api.openai.com/v1

# Run the server
uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```