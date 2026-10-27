"""wormscore: blind scoring and fluorescence measurement of neurons in confocal stacks."""
import os

# numpy's linear-algebra library (OpenBLAS) reserves a buffer for every processor core when it
# loads, about 1 GB on a 16-core computer. wormscore does no matrix math, so one thread is
# enough; this must be set before numpy is imported. A value the user set is kept.
for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

__version__ = "0.13.1"
