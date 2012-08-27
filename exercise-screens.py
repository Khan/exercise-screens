#!/usr/bin/env python

import json
import multiprocessing
import os
import re
import subprocess
import sys

import boto
from flask import Flask
from flask import abort, request, send_from_directory
from tornado.wsgi import WSGIContainer
from tornado.httpserver import HTTPServer
from tornado.ioloop import IOLoop

try:
    from secrets import AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
except ImportError:
    print "Please create a secrets.py in this directory that contains"
    print "definitions for AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY"
    sys.exit(1)


# TODO(dylan): HipChat notifications?


REPO_URL = "https://github.com/Khan/khan-exercises"
REPO_GIT_URL = "%s.git" % REPO_URL
REPO = REPO_URL.split("/")[-1]
RENDER_TIMEOUT_MS = 20000
S3_BUCKET = "ka-exercise-screenshots"
PORT = 5000
SMALL_DIMENSION = 256
GITHUB_WEBHOOK_IPS = ["207.97.227.253", "50.57.128.197", "108.171.174.178"]


app = Flask(__name__)


@app.route("/exercise-screens")
def status():
    # TODO(dylan): provide more detailed status information
    return "ok"


@app.route("/exercise-screens/exercise-file/<path:filename>")
def exercise_file(filename):
    return send_from_directory(REPO, filename)


@app.route("/exercise-screens/hook", methods=["POST"])
def hook():
    """Receive a web hook from GitHub.

    The request must originate from GitHub.com, be valid JSON, and
    pertain to the khan-exercises repository.

    Payload format: https://help.github.com/articles/post-receive-hooks
    """
    # verify hook authenticity
    if request.remote_addr not in GITHUB_WEBHOOK_IPS:
        abort(403)
    # verify payload authenticity
    try:
        payload = json.loads(request.args.get("payload"))
    except (TypeError, ValueError):
        abort(400)
    if payload["repository"]["url"] != REPO_URL:
        abort(403)
    # enqueue job for processing
    # queue is a global multiprocessing.Queue created in the main method below
    queue.put((payload["before"], payload["after"])) # @Nolint
    return "ok"


def worker(queue):
    """Worker that pulls commit ranges from the queue and processes them."""
    while True:
        # block until a job is available
        before, after = queue.get(block=True, timeout=None)
        # update the local copy of the repository
        if os.path.exists(REPO):
            # if a clone of the repo already exists, update it
            os.chdir(REPO)
            subprocess.check_call(["git", "checkout", "master"])
            subprocess.check_call(["git", "pull"])
        else:
            # make a clone of the repository
            subprocess.check_call(["git", "clone", REPO_GIT_URL])
            os.chdir(REPO)
        # determine the correct commit range
        if before is None:
            # if not specified, before is the earliest commit
            rev_list = subprocess.check_output(
                ["git", "rev-list", "--reverse", "HEAD"])
            before = rev_list.split()[0]
        if after is None:
            # if not specified, after is the latest commit
            after = subprocess.check_output(["git", "rev-parse", "HEAD"])[:-1]
        # check out the latest revision to operate on
        subprocess.check_call(["git", "checkout", after])
        # get a list of exercises to update
        exercises_to_update = plan_updates(before, after)
        # perform the updates
        for exercise in exercises_to_update:
            update(exercise)
        # mark the last commit processed
        with open("../.status", "w") as outfile:
            outfile.write(after)


def ignored_exercise(exercise):
    """Ignore khan-*.html in the exercises folder, they aren't exercises."""
    return "khan" in exercise


def plan_updates(before, after):
    """Looks at the git diff to make a plan for updating the screenshots.

    - If a global JS or CSS file is added, modified, or deleted, update all
    - If an exercises has been added or modified, update it
    - If a util is modified, update all

    TODO(dylan): when a util is modified, only the exercises that depend on it
    need to be updated

    Arguments:
        before: SHA1 of first commit in range
        after: SHA1 of last commit in range
    Returns:
        set of exercise filenames to update
    """
    to_update = set()

    all_exercises = set()
    for f in os.listdir("exercises"):
        if f.endswith(".html"):
            if not ignored_exercise(f):
                all_exercises.add(os.path.join("exercises", f))

    # look at what files have changed in the commit range
    #
    # --name-status tells us each file's name and whether it was added (A),
    # modified (M), or deleted (D)
    diff = subprocess.check_output(
        ["git", "diff", "--name-status", before, after])
    diff = [line.split() for line in diff.split("\n")[:-1]]

    global_re = re.compile(r".*\.js|css/.*\.css|css/images/.*|images/.*")
    exercise_re = re.compile(r"exercises/.*\.html")
    util_re = re.compile(r"utils/.*\.js")

    for code, path in diff:
        # A = add, M = modify, D = delete
        if code in ["A", "M", "D"] and global_re.match(path):
            return all_exercises
        if code in ["A", "M"] and exercise_re.match(path):
            if not ignored_exercise(path):
                to_update.add(path)
        if code in ["M"] and util_re.match(path):
            return all_exercises

    return to_update


def update(exercise):
    """Creates a screenshot of the exercise and updates it on S3."""
    url = "http://localhost:%s/exercise-screens/exercise-file/%s" % (
        PORT, exercise)
    exercise_name = os.path.splitext(os.path.split(exercise)[-1])[0]

    print "Updating", exercise_name

    img_name = "%s.png" % exercise_name
    resized_img_name = "%s_256.png" % exercise_name
    img_path = "../output/%s" % img_name
    resized_img_path = "../output/%s" % resized_img_name
    phantomjs_output = subprocess.check_output(
        ["phantomjs", "../rasterize.js",
            url, img_path, str(RENDER_TIMEOUT_MS)])
    if phantomjs_output != "Done\n":
        print phantomjs_output
        return

    # resize and crop image
    # see http://www.imagemagick.org/Usage/thumbnails/#cut
    resize_arg = "%sx%s^" % (SMALL_DIMENSION, SMALL_DIMENSION)
    extent_arg = "%sx%s" % (SMALL_DIMENSION, SMALL_DIMENSION)
    subprocess.check_call(["convert", "-resize", resize_arg, \
        "-extent", extent_arg, img_path, resized_img_path])

    s3 = boto.connect_s3(AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)
    bucket = s3.create_bucket(S3_BUCKET)
    # upload full-size image
    fullsize_key = boto.s3.key.Key(bucket)
    fullsize_key.key = img_name
    fullsize_key.set_contents_from_filename(img_path)
    fullsize_key.set_acl("public-read")
    # upload resized and cropped image
    resized_key = boto.s3.key.Key(bucket)
    resized_key.key = resized_img_name
    resized_key.set_contents_from_filename(resized_img_path)
    resized_key.set_acl("public-read")


def main():
    # cd to exercise-screens repo so everything else can be relative
    dir = os.path.dirname(os.path.abspath(__file__))
    if dir != '':
        os.chdir(dir)

    # start the worker subprocess
    queue = multiprocessing.Queue()
    worker_subprocess = multiprocessing.Process(target=worker, args=(queue,))
    worker_subprocess.start()

    # find the last commit processed
    if os.path.exists(".status"):
        with open(".status") as infile:
            last_processed = infile.read()
    else:
        last_processed = None

    # queue up a backfill job
    queue.put((last_processed, None))

    # run the Flask app using Tornado
    # http://flask.pocoo.org/docs/deploying/wsgi-standalone/#tornado
    HTTPServer(WSGIContainer(app)).listen(PORT)
    IOLoop.instance().start()


if __name__ == "__main__":
    main()
