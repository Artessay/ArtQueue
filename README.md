# ArtQueue

## Installation

Create a new environment and install dependencies.

```bash
# Create a new environment
conda create -n queue python=3.13 -y
conda activate queue
```

```bash
# Install dependencies
pip install -r requirements.txt
pip install -e .
```

## Usage

```bash
# set environment variables
export MAX_QPM=5  # max queries per minute
python app/main.py 
```

## Deployment

Install pyinstaller

```bash
pip install pyinstaller
```

Package the app

```bash
pyinstaller app/main.py -F --name RateLimiter
```