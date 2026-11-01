import sys

from voxcomm.skills.star_citizen.gamelog import process as _implementation

sys.modules[__name__] = _implementation
