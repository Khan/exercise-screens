# exercise-screens

## Introduction

`exercise-screens` is a utility that renders PNG screenshots of every
[static](https://github.com/Khan/khan-exercises) and
[Perseus](https://github.com/Khan/perseus) exercise on
[Khan Academy](https://khanacademy.org). It is a very simple script that
fetches a list of exercises from
[`/api/v1/exercises`](https://github.com/Khan/khan-api) and runs
[`webkit2png`](https://github.com/spicyj/webkit2png) on each one. The resulting
images are stored in an [Amazon S3](http://aws.amazon.com/s3/) bucket for use
in various places on the site.

For historical reasons, `exercise-screens` generates two versions of each
screenshot. The first is a "full-size" screenshot that can be any dimensions
but contains the entire `<div id="problemarea">`. The second is a cropped square
screenshot whose dimensions are guaranteed to be 256 by 256 pixels. However,
this behavior is easily customizable if you'd like differently sized output.

The first version of `exercise-screens` used [PhantomJS](http://phantomjs.org)
to capture screenshots, but now it uses
[spicyj's fork of `webkit2png`](https://github.com/spicyj/webkit2png)
and therefore only runs on Mac OS X now. The only other prerequisite is
[ImageMagick](http://www.imagemagick.org);
[Homebrew](https://github.com/mxcl/homebrew) users can get it by running the
command `brew install imagemagick`.

## Setting up `exercise-screens`

    git clone https://github.com/Khan/exercise-screens.git
    cd exercise-screens
    git submodule update --init
    [sudo] pip install -r requirements.txt

Then customize the S3 bucket name in `exercise-screens.py` and create a file
named `secrets.py` containing your Amazon Web Services credentials. See
`secrets.py.example` for an example of how your `secrets.py` should look.

## Running `exercise-screens`

Once your S3 credentials and bucket name are configured, simply run
`./exercise-screens.py`. The script will tell you what it is currently
processing and how many errors it encounters. An error is not the end of the
world; there currently exist some small inconsistencies in the Khan Academy
content entities that cause some exercises to have URLs that don't exist.

You might also be interested in setting up a
[custom error document](http://docs.aws.amazon.com/AmazonS3/latest/dev/CustomErrorDocSupport.html)
for your S3 bucket, which will let you serve a nice placeholder image instead
of an ugly 404 in the event that someone tries to access a screenshot that
doesn't exist.
