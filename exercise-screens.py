#!/usr/bin/env python

import multiprocessing
import sys

import requests


def process_exercise(exercise_url):
    print "Rendering %s" % exercise_url
    return True


def main():
    print "Fetching exercise data..."
    request = requests.get("http://khanacademy.org/api/v1/exercises")
    if request.status_code != 200:
        print "Error: failed to fetch exercises"
        sys.exit(1)
    exercise_urls = [e["ka_url"] for e in request.json()]
    pool = multiprocessing.Pool()
    results = pool.map(process_exercise, exercise_urls)
    success_count = results.count(True)
    failure_count = len(results) - success_count
    print "Done (%s successes, %s failures)" % (success_count, failure_count)


if __name__ == "__main__":
    main()
