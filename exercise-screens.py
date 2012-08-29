#!/usr/bin/env python

import json
import multiprocessing
import os
import re
import socket
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


"""
exercise-screens is a daemon living on KA's continuous integration machine
that keeps an up-to-date S3 bucket of screenshots of every exercise in the
khan-exercises repository. At a high level it consists of a Flask app, which
receives GitHub web hook requests when someone pushes to master, and a
singleton subprocess that asynchronously processes the changes recorded by
the Flask app.

An overview of the control flow:
1. Someone pushes to master on the khan-exercises GitHub repo.
2. GitHub POSTs to exercise-screens a pair of SHA1s corresponding to the old
     HEAD before the push and the new HEAD after the push. (For more about the
     POST contents, see https://help.github.com/articles/post-receive-hooks)
3. exercise-screens pulls the changes into its local copy of khan-exercises
     and figures out what exercises changed since the last time it ran.
4. For each of the exercises that changed, exercise-screens takes a new
     screenshot and uploads it and a downsized version to Amazon S3.
5. When exercise-screens is done processing the latest changes it writes to a
     ".status" file which lets it pick up where it left off if it gets
     interrupted.
"""


# TODO(dylan): HipChat notifications?


REPO_URL = "https://github.com/Khan/khan-exercises"
REPO_GIT_URL = "%s.git" % REPO_URL
REPO = REPO_URL.split("/")[-1]
# We tell PhantomJS to wait 20 seconds for the page to render before taking
# a screenshot because MathJax is slooooow. There is a way to fire an event
# once MathJax is done rendering but I wasn't able to get it working in
# PhantomJS.
RENDER_TIMEOUT_MS = 20000
# This is the S3 bucket where the screenshots will be uploaded.
S3_BUCKET = "ka-exercise-screenshots"
# This port on localhost is where Flask serves the exercise static files and
# PhantomJS fetches the exercise webpages.
PORT = 5000
# The resized thumbnails will be this many x this many pixels.
SMALL_DIMENSION = 256
# Any web hook request we get must originate from this hostname
WEBHOOK_ALLOWED_HOSTNAME = "github.com"


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
    hostname, _, _ = socket.gethostbyaddr(request.remote_addr)
    if not hostname.endswith(WEBHOOK_ALLOWED_HOSTNAME):
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
    old_head, new_head = payload["before"], payload["after"]
    queue.put((old_head, new_head)) # @Nolint
    return "ok"


def worker(queue):
    """Worker that pulls commit ranges from the queue and processes them."""
    while True:
        # block until a job is available
        old_head, new_head = queue.get(block=True, timeout=None)
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
        if old_head is None or new_head is None:
            # grab chronological list of commits to find the oldest and newest
            rev_list = subprocess.check_output(
                ["git", "rev-list", "HEAD"]).split()
            oldest_commit, newest_commit = rev_list[-1], rev_list[0]
            # determine the correct commit range
            if old_head is None:
                # if not specified, old_head is the oldest commit to master
                old_head = oldest_commit
            if new_head is None:
                # if not specified, new_head is the newest commit to master
                new_head = newest_commit
        # check out the latest revision to operate on
        subprocess.check_call(["git", "checkout", new_head])
        # get a list of exercises to update
        exercises_to_update = plan_updates(old_head, new_head)
        # perform the updates
        for exercise in exercises_to_update:
            update(exercise)
        # mark the last commit processed
        with open("../.status", "w") as outfile:
            outfile.write(new_head)


def should_ignore_exercise(exercise):
    """Ignore khan-*.html in the exercises folder, they aren't exercises."""
    return not exercise.endswith(".html") or "khan" in exercise


def plan_updates(old_head, new_head):
    """Looks at the git diff to make a plan for updating the screenshots.

    - If a global JS or CSS file is added, modified, or deleted, update all
    - If an exercises has been added or modified, update it
    - If a util is modified, update all

    TODO(dylan): when a util is modified, only the exercises that depend on it
    need to be updated

    Arguments:
        old_head: SHA1 of the oldest commit in the push received by GitHub
        new_head: SHA1 of the newest commit in the push received by GitHub
    Returns:
        set of exercise filenames to update
    """
    to_update = set()

    all_exercises = set()
    for f in os.listdir("exercises"):
        if not should_ignore_exercise(f):
            all_exercises.add(os.path.join("exercises", f))

    # look at what files have changed in the commit range
    #
    # --name-status tells us each file's name and whether it was added (A),
    # modified (M), or deleted (D)
    diff = subprocess.check_output(
        ["git", "diff", "--name-status", old_head, new_head])
    diff = [line.split() for line in diff.split("\n")[:-1]]

    global_re = re.compile(r".*\.js|css/.*\.css|css/images/.*|images/.*")
    exercise_re = re.compile(r"exercises/.*\.html")
    util_re = re.compile(r"utils/.*\.js")

    for code, path in diff:
        # A = add, M = modify, D = delete
        if code in ["A", "M", "D"] and global_re.match(path):
            return all_exercises
        if code in ["A", "M"] and exercise_re.match(path):
            if not should_ignore_exercise(path):
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
