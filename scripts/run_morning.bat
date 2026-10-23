@echo off
cd /d C:\Users\YHR\Agent_mix_Good_Job_Finder
"D:\miniconda\python.exe" scripts\scheduled_pipeline.py --mode morning --target 80 >> logs\pipeline_boot.log 2>&1
