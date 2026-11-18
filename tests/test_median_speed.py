"""1.26.10: the 3 x 3 median filter is worked out a faster way, with exactly the values of the earlier one."""
import numpy as np
import pytest
from scipy import ndimage

from wormscore import measure as M


@pytest.mark.parametrize("dtype", [np.uint8, np.uint16, np.int32, np.float32, np.float64])
@pytest.mark.parametrize("shape", [(1, 2, 2), (3, 2, 9), (4, 17, 3), (5, 64, 61), (2, 1, 8)])
def test_median_filter_values_are_exactly_those_of_the_earlier_method(dtype, shape):
    rng = np.random.default_rng(shape[1] * 100 + shape[2])
    for k in range(4):
        if np.issubdtype(dtype, np.integer):
            top = [np.iinfo(dtype).max, 3, 40, 4095][k]            # full range, many ties, and typical data
            stack = rng.integers(0, top + 1, shape).astype(dtype)
        else:
            stack = (rng.normal(100, 30, shape) * [1, 1e-3, 1e6, 0][k]).astype(dtype)
        want = ndimage.median_filter(stack, size=(1, 3, 3))
        got = M.median_filter_stack(stack, 3)
        assert got.dtype == want.dtype and np.array_equal(got, want)


def test_other_filter_widths_and_values_that_are_not_numbers_use_the_earlier_method():
    rng = np.random.default_rng(1)
    stack = rng.integers(0, 4096, (3, 20, 20)).astype(np.uint16)
    for size in (1, 5, 7):
        assert np.array_equal(M.median_filter_stack(stack, size), ndimage.median_filter(stack, size=(1, size, size)))
    holes = stack.astype(float)
    holes[1, 5, 5] = np.nan
    assert np.array_equal(M.median_filter_stack(holes, 3), ndimage.median_filter(holes, size=(1, 3, 3)), equal_nan=True)


def test_median_filter_on_a_large_stack_of_planes():
    """Planes are worked on by several threads at once: each must land in its own place."""
    stack = np.random.default_rng(2).integers(0, 4096, (12, 512, 512)).astype(np.uint16)
    assert np.array_equal(M.median_filter_stack(stack, 3), ndimage.median_filter(stack, size=(1, 3, 3)))
