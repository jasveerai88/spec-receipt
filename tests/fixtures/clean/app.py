"""Fixture CLI — clean state: code matches SPEC.md exactly.

The first statement below is a booby trap: it writes a
marker file if this module is ever executed or imported.
spec-receipt must never trigger it (read-only proof).
"""
from pathlib import Path

Path(__file__).with_name("EXECUTED_MARKER").write_text("ran")

import argparse


def build_parser():
    parser = argparse.ArgumentParser(prog="tool")
    parser.add_argument("--mode", default="fast")
    parser.add_argument("--level", choices=["low", "high"])
    parser.add_argument("--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("export")
    return parser


import os

from flask import Flask

app = Flask(__name__)


@app.route("/items", methods=["GET"])
def list_items():
    return []


@app.route("/items", methods=["POST"])
def create_item():
    return {}


def runtime_config(config):
    api_key = os.environ["ITEMS_API_KEY"]
    region = os.getenv("ITEMS_REGION", "us")
    host = config["server"]["host"]
    port = config.get("server.port")
    db_url = config.get("database.url")
    return api_key, region, host, port, db_url
