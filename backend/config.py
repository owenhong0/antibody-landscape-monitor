"""App configuration, loaded from the repo-root .env file."""
import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")

DATABASE_URL = os.environ["DATABASE_URL"]
RAW_DATA_DIR = REPO_ROOT / os.getenv("RAW_DATA_DIR", "data/raw")
S3_BUCKET = os.getenv("S3_BUCKET") or None