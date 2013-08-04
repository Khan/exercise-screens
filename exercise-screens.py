#!/usr/bin/env python

import os
import sys
import subprocess
import tempfile
import threading
from multiprocessing.pool import ThreadPool

import boto
import requests

try:
    from secrets import AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
except ImportError:
    print "Please create a secrets.py that looks like secrets.py.example"
    sys.exit(1)


DELAY = 5
OUTPUT_DIR = tempfile.mkdtemp()
S3_BUCKET = "ka-exercise-screenshots"
SQUARE_SIZE = 256
STDOUT_LOCK = threading.Lock()


def recolor_image(input_path, output_path, old_color, new_color):
    subprocess.check_call(
        ["convert", input_path,
            "-opaque", old_color, "-fill", new_color, output_path],
        stdout=open(os.devnull, "w"),
        stderr=open(os.devnull, "w"))


def resize_image(input_path, output_path):
    resize_arg = "%sx%s^" % (SQUARE_SIZE, SQUARE_SIZE)
    extent_arg = "%sx%s" % (SQUARE_SIZE, SQUARE_SIZE)
    subprocess.check_call(
        ["convert", "-resize", resize_arg, "-extent", extent_arg,
            input_path, output_path],
        stdout=open(os.devnull, "w"),
        stderr=open(os.devnull, "w"))


def upload_image(name, path):
    s3 = boto.connect_s3(AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)
    bucket = s3.create_bucket(S3_BUCKET)
    key = boto.s3.key.Key(bucket)
    key.key = name
    key.set_contents_from_filename(path)
    key.set_acl("public-read")


def process_exercise(exercise):
    (name, url) = exercise
    with STDOUT_LOCK:
        print "Processing %s" % name
    try:
        # Still need to shell out because PyObjC doesn't play nice with
        # multiprocessing or multithreading :(
        subprocess.check_call([
            "python",
            "./webkit2png.py",
            "--selector=#problemarea",
            "--fullsize",
            "--dir=%s" % OUTPUT_DIR,
            "--filename=%s" % name,
            "--delay=%s" % DELAY,
            url
        ],
            stdout=open(os.devnull, "w"),
            stderr=open(os.devnull, "w"))
        image_path = os.path.join(OUTPUT_DIR, "%s-full.png" % name)
        if not os.path.exists(image_path):
            return False
        recolor_image(image_path, image_path, "rgb(247,247,247)", "white")
        resized_image_path = os.path.join(OUTPUT_DIR, "%s-square.png" % name)
        resize_image(image_path, resized_image_path)
        upload_image("%s.png" % name, image_path)
        upload_image("%s_%s.png" % (name, SQUARE_SIZE), resized_image_path)
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
