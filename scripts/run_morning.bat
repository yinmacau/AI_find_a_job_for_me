@echo off
cd /d 
"D:\miniconda\python.exe" scripts\scheduled_pipeline.py --mode morning --target 80 >> logs\pipeline_boot.log 2>&1
