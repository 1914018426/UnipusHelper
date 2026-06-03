#!/usr/bin/env python3
"""Celery Worker 入口"""
import os
import sys

# 将 backend 目录加入路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.tasks.celery_app import celery_app

if __name__ == "__main__":
    celery_app.worker_main(argv=["celery", "worker", "--loglevel=info"])
