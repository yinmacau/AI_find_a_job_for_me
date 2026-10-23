@echo off
REM Agent 策略顾问：手动跑一次（只分析建议，产出 agent_strategy.json，不做任何投递）
cd /d C:\Users\YHR\Agent_mix_Good_Job_Finder
"D:\miniconda\python.exe" agent_advisor.py >> logs\advisor.log 2>&1
