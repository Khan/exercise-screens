#!/usr/bin/env python

import json
import multiprocessing
import optparse
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
    # verify hook authenticity
    if request.remote_addr not in GITHUB_WEBHOOK_IPS:
        abort(403)
    # verify payload authenticity
    try:
        payload = json.loads(request.args.get("payload"))
    except:
        abort(400)
    if payload["repository"]["url"] != REPO_URL:
        abort(403)
    # enqueue job for processing
    queue.put((payload["before"], payload["after"]))
    return "ok"


def popen_results(args):
    # from the deploy script
    # TODO(dylan): use subprocess.check_call instead?
    proc = subprocess.Popen(args, stdout=subprocess.PIPE)
    return proc.communicate()[0]


def popen_return_code(args, input=None):
    # from the deploy script
    # TODO(dylan): use subprocess.check_call instead?
    proc = subprocess.Popen(args, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE)
    proc.communicate(input)
    return proc.returncode


def worker(queue):
    """Worker that pulls commit ranges from the queue and processes them."""
    while True:
        # block until a job is available
        before, after = queue.get(block=True, timeout=None)
        # update the local copy of the repository
        if os.path.exists(REPO):
            # if a clone of the repo already exists, update it
            os.chdir(REPO)
            if popen_return_code(["git", "checkout", "master"]):
                sys.exit(1)
            if popen_return_code(["git", "pull"]):
                sys.exit(1)
        else:
            # make a clone of the repository
            if popen_return_code(["git", "clone", REPO_GIT_URL]):
                sys.exit(1)
            os.chdir(REPO)
        # determine the correct commit range
        if before is None:
            # if not specified, before is the earliest commit
            rev_list = popen_results(["git", "rev-list", "--reverse", "HEAD"])
            before = rev_list.split()[0]
        if after is None:
            # if not specified, after is the latest commit
            after = popen_results(["git", "rev-parse", "HEAD"])[:-1]
        # check out the latest revision to operate on
        if popen_return_code(["git", "checkout", after]):
            sys.exit(1)
        # get a list of exercises to update
        exercises_to_update = plan_updates(before, after)
        # perform the updates
        for exercise in exercises_to_update:
            update(exercise)
        # mark the last commit processed
        with open("../.status", "w") as outfile:
            outfile.write(after)


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
        if os.path.splitext(f)[-1] == ".html":
            # ignore khan-*.html
            if "khan" not in f:
                all_exercises.add("exercises/%s" % f)

    diff = popen_results(["git", "diff", "--name-status", before, after])
    diff = [line.split() for line in diff.split("\n")[:-1]]

    global_res = [r".*\.js", r"css/.*\.css", r"css/images/.*", r"images/.*"]
    global_res = [re.compile(r) for r in global_res]
    exercise_re = re.compile(r"exercises/.*\.html")
    util_re = re.compile(r"utils/.*\.js")

    for code, path in diff:
        if code in ["A", "M", "D"] and any([r.match(path) for r in global_res]):
            return all_exercises
        if code in ["A", "M"] and exercise_re.match(path):
            # ignore khan-*.html
            if "khan" not in path:
                to_update.add(path)
        if code in ["M"] and util_re.match(path):
            return all_exercises

    return to_update


def update(exercise):
    """Creates a screenshot of the exercise and updates it on S3."""
    url = "http://localhost:%s/exercise-screens/exercise-file/%s" % (PORT, exercise)
    exercise_name = os.path.splitext(os.path.split(exercise)[-1])[0]

    print "Updating", exercise_name

    img_name = "%s.png" % exercise_name
    resized_img_name = "%s_256.png" % exercise_name
    img_path = "../output/%s" % img_name
    resized_img_path = "../output/%s" % resized_img_name
    phantomjs_output = popen_results(
        ["phantomjs", "../rasterize.js", url, img_path, str(RENDER_TIMEOUT_MS)])
    if phantomjs_output != "Done\n":
        print phantomjs_output
        return

    # resize and crop image
    # see http://www.imagemagick.org/Usage/thumbnails/#cut
    resize_arg = "%sx%s^" % (SMALL_DIMENSION, SMALL_DIMENSION)
    extent_arg = "%sx%s" % (SMALL_DIMENSION, SMALL_DIMENSION)
    if popen_return_code(["convert", "-resize", resize_arg, \
        "-extent", extent_arg, img_path, resized_img_path]):
        print "imagemagick error"
        return

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
    dir = os.path.split(__file__)[0]
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
