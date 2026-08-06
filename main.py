"""Deprecated entry point -- use `python -m ingestion_pipeline.cli` instead.
Kept only so `python main.py` still does something sensible after `pip install -e .`"""
from ingestion_pipeline.cli import main

if __name__ == "__main__":
    main()
