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


DELAY = 4
OUTPUT_DIR = tempfile.mkdtemp()
S3_BUCKET = "ka-exercise-screenshots"
SQUARE_SIZE = 256
STDOUT_LOCK = threading.Lock()
WEBKIT2PNG_PATH = os.path.join(
                    os.path.abspath(os.path.dirname(__file__)),
                    "webkit2png.py")
IMAGEMAGICK_PATH = "/usr/local/bin/convert"


def recolor_image(input_path, output_path, old_color, new_color):
    subprocess.check_call(
        [IMAGEMAGICK_PATH, input_path, "-opaque", old_color, "-fill",
            new_color, output_path])


def trim_image(input_path, output_path):
    subprocess.check_call(
        [IMAGEMAGICK_PATH, input_path, "-trim", output_path])
    subprocess.check_call(
        [IMAGEMAGICK_PATH, output_path, "-trim", output_path])


def resize_image(input_path, output_path, bg_color):
    resize_arg = "%sx%s>" % (SQUARE_SIZE, SQUARE_SIZE)
    extent_arg = "%sx%s" % (SQUARE_SIZE, SQUARE_SIZE)
    subprocess.check_call(
        [IMAGEMAGICK_PATH, input_path, "-background", bg_color, "-gravity",
            "center", "-resize", resize_arg, "+repage", "-extent", extent_arg,
            output_path])


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
            WEBKIT2PNG_PATH,
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
        trim_image(image_path, image_path)
        resized_image_path = os.path.join(OUTPUT_DIR, "%s-square.png" % name)
        resize_image(image_path, resized_image_path, "white")
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
