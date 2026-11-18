"""All engine constants in one place. Each has a unit in its comment.

These are starting values (literature + reasoning), meant to be tuned by ear.
Milestone M2 fills in the rest of the render pipeline constants.
"""

# --- Workflow limits -------------------------------------------------------
MIN_INPUTS = 2                 # count: fewest input samples per blend
MAX_INPUTS = 4                 # count: most input samples per blend
MIN_VARIATIONS = 1             # count
MAX_VARIATIONS = 16            # count
DEFAULT_VARIATIONS = 8         # count
DEFAULT_VARIATION_AMOUNT = 50  # percent (0..100)

# --- Supported output sample rates ----------------------------------------
OUTPUT_RATES = (44100, 48000)  # Hz

# --- Keep-map --------------------------------------------------------------
KEEP_STRENGTH_MIN = 0.25       # fraction: weakest keep strength (25 %)
KEEP_STRENGTH_DEFAULT = 1.0    # fraction: default keep strength (100 %)
LOW_END_SINGLE_OWNER_HZ = 200.0  # Hz: bands with upper edge <= this get exactly one owner
