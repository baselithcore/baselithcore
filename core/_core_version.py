"""The public core release this ``core/`` tree corresponds to.

This file is byte-identical in every distribution of the framework. The public
open-core project's release job rewrites it together with ``_version.py``, so
there the two always agree. A downstream distribution that ships this ``core/``
alongside its own components receives the file through the normal core
alignment and never rewrites it: its ``_version.py`` carries the distribution's
own version, while ``CORE_VERSION`` still says which public core release the
tree contains. The system update notice compares ``CORE_VERSION`` (never the
distribution version) with the public core's releases and security advisories.
"""

CORE_VERSION = "0.43.0"
