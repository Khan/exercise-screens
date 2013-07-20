#!/usr/bin/env python

import os
import sys
import subprocess
import tempfile
import threading
from multiprocessing.pool import ThreadPool

import requests

DELAY = 5
STDOUT_LOCK = threading.Lock()


def process_exercise(exercise):
    (name, url) = exercise
    with STDOUT_LOCK:
        print "Rendering %s" % name
    try:
        output_dir = tempfile.mkdtemp()
        # Still need to shell out because PyObjC doesn't play nice with
        # multiprocessing or multithreading :(
        subprocess.check_call([
            "python",
            "./webkit2png.py",
            "--selector=#problemarea",
            "--fullsize",
            "--dir=%s" % output_dir,
            "--filename=%s" % name,
            "--delay=%s" % DELAY,
            url
        ],
            stdout=open(os.devnull, "w"),
            stderr=open(os.devnull, "w"))
        image_path = os.path.join(output_dir, "%s-full.png" % name)
        # TODO: remove image border?
        # TODO: save image at a few different sizes?
        # TODO: upload %(image_path) to S3
        os.remove(image_path)
    except:
        return False
    return True


def main():
    print "Fetching exercise data..."
    request = requests.get("http://khanacademy.org/api/v1/exercises")
    if request.status_code != 200:
        print "Error: failed to fetch exercises"
        sys.exit(1)
    exercises = [(e["name"], e["ka_url"]) for e in request.json()]
    pool = ThreadPool()
    try:
        # see http://stackoverflow.com/a/1408476
        results = pool.map_async(process_exercise, exercises).get(99999)
    except KeyboardInterrupt:
        sys.exit(1)
    success_count = results.count(True)
    failure_count = len(results) - success_count
    print "Done (%s successes, %s failures)" % (success_count, failure_count)


if __name__ == "__main__":
    main()
