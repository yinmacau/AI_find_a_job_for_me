@echo off
cd /d C:
"D:\miniconda\python.exe" scripts\scheduled_pipeline.py --mode evening >> logs\pipeline_boot.log 2>&1
