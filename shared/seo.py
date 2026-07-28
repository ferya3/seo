"""The numbers that define what "good" means, in one place.

These are judgements, not facts — Google publishes no character limit, and
these bands come from where snippets are typically truncated and from what
reads as a real title rather than a stub. That is exactly why they belong in
one module: three copies of "a title should be 25 to 60 characters" is three
places to disagree, and a report that grades a page differently from the
service proposing its rewrite is a report nobody trusts.

The engine, the optimizer and the competitor service all read from here.
"""

from __future__ import annotations

# Titles: below the floor a title is a stub carrying no query terms; above the
# ceiling the end is cut off in results, so anything that matters must sit
# before it.
TITLE_MIN = 25
TITLE_MAX = 60

# Descriptions: shorter wastes snippet space that is already yours, longer is
# truncated. Neither is a ranking factor; both change how many people click.
DESC_MIN = 70
DESC_MAX = 160

# Below this a page has too little to be the best answer to anything. 300 is a
# working line, not a threshold Google publishes.
THIN_WORDS = 300
