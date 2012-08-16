// based on http://code.google.com/p/phantomjs/wiki/QuickStart

var page = require('webpage').create(),
    address, output, timeout_ms;

if (phantom.args.length < 3 || phantom.args.length > 4) {
    console.log('Usage: rasterize.js URL filename timeout_ms');
    phantom.exit();
} else {
    address = phantom.args[0];
    output = phantom.args[1];
    timeout_ms = phantom.args[2];
    page.viewportSize = { width: 1024, height: 768 };
    page.open(address, function (status) {
        if (status !== 'success') {
            console.log('Unable to load the address!');
        } else {
            window.setTimeout(function () {
                page.clipRect = page.evaluate(function () {
                    var problemarea = $('#problemarea');
                    var offset = problemarea.offset();
                    return {
                        top: offset.top,
                        left: offset.left,
                        width: problemarea.width(),
                        height: problemarea.height()
                    }
                });
                page.render(output);
                console.log('Done');
                phantom.exit();
            }, timeout_ms);
        }
    });
}
