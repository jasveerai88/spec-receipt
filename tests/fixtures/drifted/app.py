"""Fixture CLI — drifted state: the code evolved against
the frozen SPEC.md. One option was deleted, one default
changed, a forbidden option appeared, and an
undocumented option was added.

The first statement below is a booby trap: it writes a
marker file if this module is ever executed or imported.
spec-receipt must never trigger it (read-only proof).
"""
from pathlib import Path

Path(__file__).with_name("EXECUTED_MARKER").write_text("ran")

import argparse


def build_parser():
    parser = argparse.ArgumentParser(prog="tool")
    parser.add_argument("--mode", default="slow")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--legacy", action="store_true")
    parser.add_argument("--extra", default="x")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("export")
    return parser


import os

from flask import Flask

app = Flask(__name__)


@app.route("/items", methods=["GET"])
def list_items():
    return []


# Drift: the create handler was changed from POST to
# GET against the frozen spec.
@app.route("/items", methods=["GET"])
def create_item():
    return {}


def runtime_config(config):
    api_key = os.environ["ITEMS_API_KEY"]
    region = os.getenv("ITEMS_REGION", "us")
    host = config["server"]["host"]
    port = config.get("server.port")
    db_url = config.get("database.url")
    return api_key, region, host, port, db_url
